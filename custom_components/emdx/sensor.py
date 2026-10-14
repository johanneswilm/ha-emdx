"""Sensors for Samsung EMDX panels."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .entity import EmdxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data["coordinator"]
    async_add_entities(
        [
            EmdxBatterySensor(entry, coordinator),
            EmdxFirmwareSensor(entry, coordinator),
        ]
    )


class EmdxBatterySensor(EmdxEntity, SensorEntity):
    """Battery level; the EM32DX runs on a built-in pack."""

    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_attribution = "MDC 0x1B/0x73"

    @property
    def native_value(self) -> StateType:
        battery = (self._coordinator.data or {}).get("battery")
        return battery["level"] if battery else None

    @property
    def extra_state_attributes(self) -> dict:
        battery = (self._coordinator.data or {}).get("battery")
        if not battery:
            return {}
        return {
            "charging": battery["charging"],
            "present": battery["present"],
            "healthy": battery["healthy"],
        }


class EmdxFirmwareSensor(EmdxEntity, SensorEntity):
    _attr_translation_key = "firmware"
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> StateType:
        return (self._coordinator.data or {}).get("sw_version")
