"""Config flow for Samsung EMDX e-paper displays."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PIN
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import CONF_CONTENT_PORT, DEFAULT_CONTENT_PORT, DOMAIN
from .mdc import MdcAuthError, MdcConnectionError, MdcDevice

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PIN): cv.string,
        vol.Optional(CONF_MAC): cv.string,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PIN): cv.string})


class EmdxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def _validate(self, host: str, pin: str) -> str:
        """Connect + authenticate; return the serial number."""
        device = MdcDevice(host, pin)
        await device.connect(attempts=2, retry_delay=3)
        try:
            return await device.get_serial()
        finally:
            await device.disconnect()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            pin = str(user_input[CONF_PIN]).strip()
            try:
                serial = await self._validate(host, pin)
            except MdcAuthError:
                errors["base"] = "invalid_auth"
            except MdcConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self._async_set_unique_id_and_abort(host, serial)
                return self.async_create_entry(
                    title=f"Samsung EMDX {serial[-4:]}",
                    data={
                        CONF_HOST: host,
                        CONF_PIN: pin,
                        CONF_MAC: user_input.get(CONF_MAC),
                        CONF_CONTENT_PORT: DEFAULT_CONTENT_PORT,
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @callback
    def _async_set_unique_id_and_abort(self, host: str, serial: str) -> None:
        self._async_set_unique_id(serial)
        self._async_abort_entries_match({CONF_HOST: host})

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = str(user_input[CONF_PIN]).strip()
            try:
                await self._validate(entry.data[CONF_HOST], pin)
            except MdcAuthError:
                errors["base"] = "invalid_auth"
            except MdcConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_PIN: pin}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )
