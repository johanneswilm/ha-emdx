"""Shared device wiring for EMDX entities."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import EmdxCoordinator


class EmdxEntity:
    """Mixin providing the device entry shared by all entities of one panel."""

    def __init__(self, entry: ConfigEntry, coordinator: EmdxCoordinator) -> None:
        self._entry = entry
        self._coordinator = coordinator
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
        return bool(self._coordinator.last_successful_update is not None) and bool(
            (self._coordinator.data or {}).get("available", False)
        )
