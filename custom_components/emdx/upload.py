"""Dashboard upload endpoint and Lovelace card module.

The card (served unauthenticated — it contains no secrets) lets a user pick
an image file; the browser downscales it to the panel resolution and POSTs
the bytes here. We store them as the single "last upload" (each upload
replaces the previous) and push them through the normal push_image service.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from aiohttp import web
from PIL import Image
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILE = Path(__file__).parent / "push_card.js"
MANIFEST = Path(__file__).parent / "manifest.json"
UPLOAD_SUBDIR = "emdx"
UPLOAD_NAME = "last_upload.jpg"


class UploadView(HomeAssistantView):
    """POST raw image bytes; store and push to the given EMDX entity."""

    url = "/api/emdx/upload"
    name = "api:emdx:upload"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        hass = self._hass
        entity_id = request.query.get("entity_id")
        if not entity_id:
            return self.json_message("entity_id query parameter required", 400)
        ent = er.async_get(hass).async_get(entity_id)
        entry = (
            hass.config_entries.async_get_entry(ent.config_entry_id)
            if ent and ent.config_entry_id
            else None
        )
        if entry is None or entry.domain != DOMAIN:
            return self.json_message(f"{entity_id} is not an EMDX entity", 400)
        body = await request.read()
        if not body:
            return self.json_message("Empty upload", 400)
        try:
            Image.open(io.BytesIO(body)).verify()
        except Exception:
            return self.json_message("Upload is not a readable image", 400)

        path = hass.config.path(UPLOAD_SUBDIR, UPLOAD_NAME)

        def _write() -> None:
            dest = Path(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)

        await hass.async_add_executor_job(_write)

        try:
            await hass.services.async_call(
                DOMAIN,
                "push_image",
                {"entity_id": entity_id, "image": path},
                blocking=True,
            )
        except HomeAssistantError as err:
            return self.json_message(f"Upload saved, push failed: {err}", 500)
        return self.json({"status": "pushed", "bytes": len(body)})


class CardView(HomeAssistantView):
    """Serve the Lovelace card module."""

    url = "/emdx/card.js"
    name = "api:emdx:card"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(
            text=CARD_FILE.read_text(encoding="utf-8"),
            content_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )


def card_url(hass: HomeAssistant) -> str:
    """Card URL with a version query so browsers refetch after upgrades."""
    try:
        version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    except Exception:
        version = "dev"
    return f"/emdx/card.js?v={version}"


async def ensure_card_resource(hass: HomeAssistant) -> None:
    """Register the card module as a Lovelace resource (storage mode).

    Resources live in the backend, so this covers desktop browsers and the
    mobile app alike, and survives dashboard cleanups that drop the card.
    Idempotent: matches on the URL path and refreshes the ?v= query so
    browsers refetch after integration upgrades.
    """
    lovelace_data = hass.data.get("lovelace")
    if isinstance(lovelace_data, dict):  # HA < 2024.3 kept a plain dict
        resources = lovelace_data.get("resources")
    else:
        resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        _LOGGER.warning("%s: lovelace not set up; push card unavailable", DOMAIN)
        return
    if not hasattr(resources, "async_create_item"):
        # YAML-managed resources (lovelace: resource_mode: yaml) — read-only.
        _LOGGER.info(
            "%s: Lovelace resources are YAML-managed; add a module resource"
            " with url %s to enable the push card",
            DOMAIN,
            card_url(hass),
        )
        return
    url = card_url(hass)
    path = url.split("?", 1)[0]
    await resources.async_get_info()  # forces storage load before async_items()
    for item in resources.async_items():
        if item["url"].split("?", 1)[0] != path:
            continue
        if item["url"] != url:
            await resources.async_update_item(item["id"], {"url": url})
        return
    await resources.async_create_item({"res_type": "module", "url": url})


def register_views(hass: HomeAssistant) -> None:
    if hass.data[DOMAIN].get("web_ui_registered"):
        return
    hass.http.register_view(UploadView(hass))
    hass.http.register_view(CardView())
    hass.data[DOMAIN]["web_ui_registered"] = True
