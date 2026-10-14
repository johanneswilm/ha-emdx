"""Samsung EMDX e-paper display integration."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.components import camera, network
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PIN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.selector import TargetSelector, TargetSelectorConfig

from .const import (
    DOMAIN,
    SERVICE_PUSH_IMAGE,
    SERVICE_SLEEP,
    SERVICE_WAKE,
)
from .content_server import ContentServer, build_manifest
from .coordinator import EmdxCoordinator
from .image import prepare_image
from .mdc import MdcAuthError, MdcConnectionError, MdcDevice, MdcError, send_wol

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]

_TARGET = TargetSelector(
    TargetSelectorConfig(entity={"integration": DOMAIN})
)

PUSH_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("target"): _TARGET,
        vol.Exclusive("image", "source"): cv.string,
        vol.Exclusive(CONF_URL, "source"): cv.string,
        vol.Exclusive("camera_entity", "source"): cv.entity_id,
        vol.Optional("token"): cv.string,
        vol.Optional("resize", default=True): cv.boolean,
        vol.Optional("quality", default=90): vol.All(
            vol.Coerce(int), vol.Range(30, 100)
        ),
        vol.Optional("sleep_after", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)

TARGET_SCHEMA = vol.Schema({vol.Required("target"): _TARGET})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    pin = entry.data[CONF_PIN]

    device = MdcDevice(host, pin)
    coordinator = EmdxCoordinator(hass, device)
    hass.data.setdefault(DOMAIN, {})
    server = ContentServer(hass)
    server.start()

    entry.runtime_data = {
        "device": device,
        "coordinator": coordinator,
        "server": server,
    }

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise

    if not hass.data[DOMAIN].get("services_registered"):
        _register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload:
        entry.runtime_data.pop("server", None)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not any(k for k in hass.data[DOMAIN] if k != "services_registered"):
            for svc in (SERVICE_PUSH_IMAGE, SERVICE_WAKE, SERVICE_SLEEP):
                hass.services.async_remove(DOMAIN, svc)
    return unload


def _register_services(hass: HomeAssistant) -> None:
    async def _resolve_entries(call: ServiceCall) -> list[ConfigEntry]:
        entry_ids: set[str] = set()
        target = call.data.get("target") or {}
        registry = er.async_get(hass)
        for key in ("config_entry_id", "entity_id", "device_id"):
            value = target.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                value = [value]
            for item in value:
                if key == "config_entry_id":
                    entry_ids.add(item)
                elif key == "entity_id":
                    ent = registry.async_get(item)
                    if ent and ent.config_entry_id:
                        entry_ids.add(ent.config_entry_id)
                else:
                    for ent in registry.async_entries_for_device(item):
                        if ent.config_entry_id:
                            entry_ids.add(ent.config_entry_id)
        entries = [
            entry
            for eid in entry_ids
            if (entry := hass.config_entries.async_get_entry(eid)) is not None
            and entry.domain == DOMAIN
        ]
        if not entries:
            raise HomeAssistantError("No Samsung EMDX entry matched the target")
        return entries

    async def _load_image(hass: HomeAssistant, data: dict) -> bytes:
        if "image" in data:
            path = data["image"]

            def _read() -> bytes:
                with open(path, "rb") as f:
                    return f.read()

            return await hass.async_add_executor_job(_read)
        if CONF_URL in data:
            session = async_get_clientsession(hass)
            resp = await session.get(data[CONF_URL])
            resp.raise_for_status()
            return await resp.read()
        if "camera_entity" in data:
            img = await camera.async_snapshot(hass, data["camera_entity"])
            if not img:
                raise HomeAssistantError("Camera snapshot failed")
            return img
        raise HomeAssistantError(
            "Provide one of: image (file path), url, camera_entity"
        )

    async def _push_image(hass: HomeAssistant, entry: ConfigEntry, data: dict) -> None:
        device: MdcDevice = entry.runtime_data["device"]
        coordinator: EmdxCoordinator = entry.runtime_data["coordinator"]
        server: ContentServer = entry.runtime_data["server"]

        raw = await _load_image(hass, data)
        jpeg = await hass.async_add_executor_job(
            prepare_image,
            raw,
            data.get("resize", True),
            data.get("quality", 90),
        )

        local_ip = network.async_get_source_ip(hass, entry.data[CONF_HOST])
        content_url = server.stage(
            jpeg, local_ip, hass.http.server_port, data.get("token")
        )
        image_url = content_url.removesuffix("/content.json") + "/image"
        server.commit(content_url, build_manifest(image_url, len(jpeg)))

        _LOGGER.info("Pushing image: display will fetch %s", content_url)
        requests_before = server.request_count
        async with coordinator.lock:
            try:
                await device.connect()
                try:
                    await device.set_content_download(content_url)
                finally:
                    await device.disconnect()
            except MdcAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except MdcConnectionError as err:
                raise HomeAssistantError(
                    f"Display unreachable (asleep?): {err}"
                ) from err

        fetched = await server.wait_fetched(content_url, timeout=60)
        if not fetched:
            raise HomeAssistantError(
                f"Display did not fetch the image (content endpoint saw "
                f"{server.request_count - requests_before} requests) — check "
                "it is powered and on the same network (or wake it first)."
            )
        _LOGGER.info("Pushed image to %s", entry.title)

        sleep_after = int(data.get("sleep_after") or 0)
        if sleep_after > 0:
            asyncio.get_running_loop().call_later(
                sleep_after * 60,
                lambda: hass.async_create_task(_sleep_entry(hass, entry)),
            )

    async def _sleep_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
        device: MdcDevice = entry.runtime_data["device"]
        coordinator: EmdxCoordinator = entry.runtime_data["coordinator"]
        async with coordinator.lock:
            try:
                await device.connect(attempts=1)
                try:
                    await device.set_network_standby(False)
                    await device.set_power(False)
                finally:
                    await device.disconnect()
            except MdcError as err:
                _LOGGER.warning("Auto-sleep failed for %s: %s", entry.title, err)

    async def handle_push_image(call: ServiceCall) -> None:
        for entry in await _resolve_entries(call):
            await _push_image(hass, entry, call.data)

    async def handle_wake(call: ServiceCall) -> None:
        for entry in await _resolve_entries(call):
            mac = entry.data.get(CONF_MAC)
            if not mac:
                raise HomeAssistantError("wake requires a MAC address in the entry")
            await send_wol(mac)
            _LOGGER.info("Sent Wake-on-LAN to %s", entry.title)

    async def handle_sleep(call: ServiceCall) -> None:
        for entry in await _resolve_entries(call):
            await _sleep_entry(hass, entry)

    async def handle_debug_info(call: ServiceCall) -> dict:
        entry = await _resolve_entries(call)
        server: ContentServer = entry[0].runtime_data["server"]
        return {
            "request_count": server.request_count,
            "last_content_url": server.last_content_url,
        }

    hass.services.async_register(
        DOMAIN, "debug_info", handle_debug_info, TARGET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PUSH_IMAGE, handle_push_image, PUSH_IMAGE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_WAKE, handle_wake, TARGET_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SLEEP, handle_sleep, TARGET_SCHEMA)
