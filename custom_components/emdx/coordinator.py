"""Data coordinator for Samsung EMDX displays."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .mdc import MdcAuthError, MdcConnectionError, MdcDevice, MdcError
_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=15)


class EmdxCoordinator(DataUpdateCoordinator[dict]):
    """Poll the small readable surface (battery, network standby, name).

    The display throttles rapid commands and only serves one MDC session at a
    time, so polling is gentle and every read opens a fresh session.
    """

    def __init__(self, hass: HomeAssistant, device: MdcDevice) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=device.host,
            update_interval=POLL_INTERVAL,
        )
        self.device = device
        self.lock = asyncio.Lock()
        self.auth_failed = False

    def async_add_listener(self, update_callback, context=None):
        """Wrap add/remove listener with diagnostic logs (temporary)."""

        def _diag_cb() -> None:
            _LOGGER.warning("emdx diag: listener fired")
            update_callback()

        unsub = super().async_add_listener(_diag_cb, context)
        _LOGGER.warning(
            "emdx diag: listener added (total=%d)", len(self._listeners)
        )

        def _unsub() -> None:
            unsub()
            _LOGGER.warning(
                "emdx diag: listener removed (total=%d)", len(self._listeners)
            )

        return _unsub

    def schedule_late_refresh(self, delay: float = 45.0) -> None:
        """Re-check state after a power change.

        The display keeps serving its last (now dead) session and closes the
        MDC port ~30-60 s later, so the immediate post-sleep refresh races
        the shutdown.
        """
        self.hass.loop.call_later(
            delay,
            lambda: self.hass.async_create_task(self.async_request_refresh()),
        )

    async def _async_update_data(self) -> dict:
        _LOGGER.warning("emdx diag: update requested (device lock busy=%s)", self.lock.locked())
        async with self.lock:
            try:
                await self.device.connect()
                try:
                    data: dict = {}
                    try:
                        data["battery"] = await self.device.get_battery()
                    except MdcError:
                        data["battery"] = None
                    try:
                        data["network_standby"] = await self.device.get_network_standby()
                    except MdcError:
                        data["network_standby"] = None
                    try:
                        data["name"] = await self.device.get_device_name()
                    except MdcError:
                        data["name"] = None
                    try:
                        data["sw_version"] = await self.device.get_sw_version()
                    except MdcError:
                        data["sw_version"] = None
                finally:
                    await self.device.disconnect()
            except MdcAuthError as err:
                self.auth_failed = True
                raise ConfigEntryAuthFailed(f"PIN rejected by display: {err}") from err
            except MdcConnectionError:
                # Display is likely in deep sleep (network standby off):
                # unreachable is a normal state, not an error.
                self.auth_failed = False
                _LOGGER.warning("emdx diag: update done: unreachable")
                return {"available": False}
            self.auth_failed = False
            data["available"] = True
            _LOGGER.warning("emdx diag: update done: %r", data)
            return data
