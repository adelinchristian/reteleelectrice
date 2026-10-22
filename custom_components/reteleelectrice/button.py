"""
Platforma Button pentru Rețele Electrice România.

Creează un buton per POD (smart meter) pentru a forța
actualizarea valorilor instantanee de la contor.
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, LICENSE_DATA_KEY
from .coordinator import ReteleElectriceCoordinator, _vf_failed
from .helpers import build_pod_address

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configurare butoane: un buton per POD smart meter."""
    coordinator: ReteleElectriceCoordinator = config_entry.runtime_data

    if not coordinator.data:
        _LOGGER.warning("[ReteleElectrice] Button: coordinator.data este gol")
        return

    pods = coordinator.data.get("pods", [])
    # None / listă goală = toate POD-urile (înainte: `pod in None` → TypeError
    # și platforma de butoane nu se mai încărca)
    selected = coordinator.data.get("selected_pods") or []
    account_name = ""
    account = coordinator.data.get("account_info")
    if account and isinstance(account, dict):
        account_name = account.get("Name", "")

    entities: list[ButtonEntity] = []

    for pod in pods:
        pod_name = pod.get("Name") or pod.get("POD__c", "")
        if not pod_name or (selected and pod_name not in selected):
            continue

        is_smart = pod.get("Smart_meter__c", False) or pod.get(
            "IsSmartMeter__c", False
        )

        if not is_smart:
            continue

        address = build_pod_address(pod)

        entities.append(
            ActualizareInstantaneeButton(
                coordinator, config_entry, pod_name, pod, account_name, address
            )
        )

    _LOGGER.debug(
        "[ReteleElectrice] Se adaugă %d butoane (entry_id=%s)",
        len(entities),
        config_entry.entry_id,
    )

    async_add_entities(entities)


class ActualizareInstantaneeButton(
    CoordinatorEntity[ReteleElectriceCoordinator], ButtonEntity
):
    """Buton pentru forțarea actualizării valorilor instantanee."""

    _attr_has_entity_name = True
    _attr_translation_key = "actualizare_instantanee"
    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        coordinator: ReteleElectriceCoordinator,
        config_entry: ConfigEntry,
        pod_name: str,
        pod_data: dict,
        account_name: str,
        address: str,
    ) -> None:
        """Inițializare buton actualizare instantanee."""
        super().__init__(coordinator)
        self._pod_name = pod_name
        self._pod_data = pod_data
        self._config_entry = config_entry
        self._account_name = account_name
        self._address = address
        self._attr_unique_id = f"{DOMAIN}_{pod_name.lower()}_actualizare_instantanee"

    @property
    def device_info(self) -> DeviceInfo:
        """Asociere la device-ul POD-ului."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._pod_name)},
            name=f"Rețele Electrice {self._pod_name}",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="Rețele Electrice România",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _is_license_valid(self) -> bool:
        """Verifică dacă licența este validă."""
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        return mgr is not None and mgr.is_valid

    async def async_press(self) -> None:
        """Forțează actualizarea valorilor instantanee.

        Orice eșec ridică HomeAssistantError → mesajul apare în interfață
        (înainte erorile ajungeau doar în log).
        """
        if not self._is_license_valid():
            raise HomeAssistantError(
                f"Licența nu este validă — actualizarea instantanee e blocată (POD {self._pod_name})."
            )

        _LOGGER.info(
            "[ReteleElectrice] Buton apăsat: actualizare instantanee pentru POD %s",
            self._pod_name,
        )

        # Obținem CNP din datele contului
        cnp = ""
        account = (self.coordinator.data or {}).get("account_info")
        if account and isinstance(account, dict):
            cnp = account.get("CNP__c", "") or account.get("Fiscal_Code__c", "")

        try:
            result = await self.coordinator.api.async_get_instant_values(
                self._pod_name, cnp=cnp
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Eroare la actualizarea valorilor instantanee (POD {self._pod_name}): {err}"
            ) from err

        if _vf_failed(result):
            # Înainte răspunsul de eroare înlocuia valorile bune afișate de senzori
            raise HomeAssistantError(
                f"Portalul Rețele Electrice nu a returnat valori instantanee "
                f"pentru POD {self._pod_name}. Încercați din nou mai târziu."
            )

        # Actualizăm datele din coordinator (copie — nu modificăm datele în loc)
        data = dict(self.coordinator.data or {})
        data["instant_values"] = {**(data.get("instant_values") or {}), self._pod_name: result}
        self.coordinator.async_set_updated_data(data)
        _LOGGER.debug(
            "[ReteleElectrice] Valori instantanee actualizate cu succes pentru POD %s",
            self._pod_name,
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Atribute suplimentare."""
        return {
            "POD": self._pod_name,
            "attribution": ATTRIBUTION,
        }
