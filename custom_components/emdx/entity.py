"""Shared device wiring for EMDX entities."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EmdxCoordinator


class EmdxEntity(CoordinatorEntity[EmdxCoordinator]):
    """Mixin providing the device entry + the coordinator subscription.

    Deriving from CoordinatorEntity is what makes live updates work: a
    DataUpdateCoordinator only (re)schedules its interval refresh while it has
    listeners and notifies only listeners when data changes. Plain mixin
    entities register nothing, so polling never continues past the setup
    first-refresh and no entity state is ever rewritten.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: EmdxCoordinator,
        key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._coordinator = coordinator  # shorthand used by platform classes
        if key:
            self._attr_unique_id = f"{entry.unique_id or entry.entry_id}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="EMDX e-paper",
            sw_version=coordinator.data.get("sw_version") if coordinator.data else None,
        )
        if entry.data.get("mac"):
            self._attr_device_info["connections"] = {
                ("mac", entry.data["mac"].lower())
            }

    @property
    def available(self) -> bool:
        # Unreachable == deep sleep with network standby off; that is normal.
        return bool((self._coordinator.data or {}).get("available", False))
