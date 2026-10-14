"""Switch entities for Samsung EMDX panels."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EmdxCoordinator
from .entity import EmdxEntity
from .mdc import MdcError, send_wol

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmdxCoordinator = entry.runtime_data["coordinator"]
    async_add_entities(
        [
            EmdxPowerSwitch(entry, coordinator),
            EmdxNetworkStandbySwitch(entry, coordinator),
            EmdxDeepSleepSwitch(entry, coordinator),
        ]
    )


class _SessionSwitch(EmdxEntity, SwitchEntity):
    """Base that serialises MDC commands behind the coordinator lock."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, coordinator: EmdxCoordinator) -> None:
        super().__init__(entry, coordinator, self._attr_translation_key)
        self._device = entry.runtime_data["device"]

    async def _session_command(self, coro_name: str, *args) -> None:
        async with self._coordinator.lock:
            try:
                await self._device.connect()
                try:
                    await getattr(self._device, coro_name)(*args)
                finally:
                    await self._device.disconnect()
            except MdcError as err:
                raise HomeAssistantError(f"{self.name}: {err}") from err


class EmdxPowerSwitch(_SessionSwitch):
    """Panel power. The display never reports its power state (0x11 get is
    NAK'd), so this switch is optimistic."""

    _attr_translation_key = "power"
    _attr_icon = "mdi:power"

    def __init__(self, entry, coordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_is_on = True

    async def async_turn_on(self, **kwargs) -> None:
        await self._session_command("set_power", True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self._session_command("set_power", False)
        self._attr_is_on = False
        self.async_write_ha_state()


class EmdxNetworkStandbySwitch(_SessionSwitch):
    """WiFi listener during sleep (MDC 0xB5). On = reachable/WoL-capable."""

    _attr_translation_key = "network_standby"
    _attr_icon = "mdi:wifi"

    @property
    def is_on(self) -> bool | None:
        data = self._coordinator.data or {}
        return data.get("network_standby")

    async def async_turn_on(self, **kwargs) -> None:
        await self._session_command("set_network_standby", True)
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._session_command("set_network_standby", False)
        await self._coordinator.async_request_refresh()


class EmdxDeepSleepSwitch(_SessionSwitch):
    """True deep sleep: network standby off + panel power off.

    On = sleeping and unreachable: Wake-on-LAN does NOT work with the
    standby listener off; wake needs the physical button (or an on-timer).
    Off = network standby enabled and panel powered (WoL attempted first,
    harmless when the panel is already awake).
    """

    _attr_translation_key = "deep_sleep"
    _attr_icon = "mdi:power-sleep"

    @property
    def is_on(self) -> bool | None:
        standby = (self._coordinator.data or {}).get("network_standby")
        if standby is None:
            # Unreachable: assume it is sleeping if we could not reach it.
            return None
        return not standby

    async def async_turn_on(self, **kwargs) -> None:
        """Enter deep sleep."""
        async with self._coordinator.lock:
            try:
                await self._device.connect()
                try:
                    await self._device.set_network_standby(False)
                    await self._device.set_power(False)
                finally:
                    await self._device.disconnect()
            except MdcError as err:
                raise HomeAssistantError(f"{self.name}: {err}") from err
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Wake up: WoL, then re-enable network standby and power on."""
        mac = self._entry.data.get("mac")
        if not mac:
            raise HomeAssistantError(
                "Wake requires the MAC address; edit this integration entry to add it."
            )
        try:
            await send_wol(mac)
        except OSError as err:
            raise HomeAssistantError(f"Wake-on-LAN failed: {err}") from err
        await asyncio.sleep(5)
        async with self._coordinator.lock:
            try:
                await self._device.connect()
                try:
                    await self._device.set_network_standby(True)
                    await self._device.set_power(True)
                finally:
                    await self._device.disconnect()
            except MdcError as err:
                raise HomeAssistantError(f"{self.name}: {err}") from err
        await self._coordinator.async_request_refresh()
