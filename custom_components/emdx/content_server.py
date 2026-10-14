"""Content endpoints served on Home Assistant's own HTTP port.

The display only speaks the "content download" flow: MDC 0xC7 hands it a URL
to a manifest, then it GETs the manifest and the image it references. HAOS
firewalls everything except 8123, so instead of a separate listener we hook
two unauthenticated views into hass.http. Each push gets a random token that
both URLs share.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import math

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONTENT_TTL = 600  # seconds a superseded push stays fetchable
URL_PREFIX = "/emdx/c/"


class ContentServer:
    """Pending pushes keyed by token, served under /emdx/c/<token>/."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._pending: dict[str, dict] = {}

        self.request_count = 0
        self.last_content_url: str | None = None

    def start(self) -> None:
        if self._hass.data[DOMAIN].get("views_registered"):
            return
        self._hass.http.register_view(_ManifestView(self))
        self._hass.http.register_view(_ImageView(self))
        self._hass.data[DOMAIN]["views_registered"] = True

    def stage(self, image: bytes, host: str, port: int) -> str:
        """Stage one push; returns the content.json URL to hand the display.

        The newest content stays fetchable forever: the display keeps the URL
        and re-fetches it (e.g. after wake); a 404 shows "Configuration error"
        on the panel. Superseded pushes expire after CONTENT_TTL.
        """
        now = time.monotonic()
        self._pending = {
            token: entry
            for token, entry in self._pending.items()
            if entry["expires"] > now
        }
        token = secrets.token_urlsafe(16)
        base = f"http://{host}:{port}{URL_PREFIX}{token}"
        self.last_content_url = f"{base}/content.json"
        self._pending[token] = {
            "manifest": None,
            "image": image,
            "expires": math.inf,
            "fetched": asyncio.Event(),
        }
        return f"{base}/content.json"

    @staticmethod
    def token_of(content_url: str) -> str:
        return content_url.split(URL_PREFIX, 1)[1].split("/", 1)[0]

    def commit(self, content_url: str, manifest: dict) -> None:
        """Attach the manifest that references the staged image URL."""
        entry = self._pending.get(self.token_of(content_url))
        if entry is not None:
            entry["manifest"] = manifest

    async def wait_fetched(self, content_url: str, timeout: float) -> bool:
        entry = self._pending.get(self.token_of(content_url))
        if entry is None:
            return False
        try:
            await asyncio.wait_for(entry["fetched"].wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def get_pending(self, token: str) -> dict | None:
        self.request_count += 1
        entry = self._pending.get(token)
        if entry is None or entry["expires"] <= time.monotonic():
            return None
        return entry


class _ManifestView(HomeAssistantView):
    url = f"{URL_PREFIX}{{token}}/content.json"
    name = "emdx:content-manifest"
    requires_auth = False

    def __init__(self, server: ContentServer) -> None:
        self._server = server

    async def get(self, request: web.Request, token: str) -> web.Response:
        entry = self._server.get_pending(token)
        if entry is None or entry["manifest"] is None:
            return web.Response(status=403)
        body = json.dumps(entry["manifest"]).replace("/", "\\/")
        _LOGGER.info("EMDX fetched content.json")
        return web.Response(text=body, content_type="application/json")


class _ImageView(HomeAssistantView):
    url = f"{URL_PREFIX}{{token}}/image"
    name = "emdx:content-image"
    requires_auth = False

    def __init__(self, server: ContentServer) -> None:
        self._server = server

    async def get(self, request: web.Request, token: str) -> web.Response:
        entry = self._server.get_pending(token)
        if entry is None:
            return web.Response(status=403)
        _LOGGER.info("EMDX fetched image (%d bytes)", len(entry["image"]))
        entry["fetched"].set()
        return web.Response(body=entry["image"], content_type="image/jpeg")


def build_manifest(image_url: str, image_size: int, *, name: str = "home-assistant") -> dict:
    """The manifest shape the Samsung e-paper app uses (deploy_type MOBILE)."""
    file_id = secrets.token_hex(8).upper()
    fname = f"{file_id}.jpg"
    return {
        "schedule": [
            {
                "start_date": "1970-01-01",
                "stop_date": "2999-12-31",
                "start_time": "00:00:00",
                "contents": [
                    {
                        "image_url": image_url,
                        "file_id": file_id,
                        "file_path": "/home/owner/content/Downloads/vxtplayer/"
                        f"epaper/mobile/contents/{file_id}/{fname}",
                        "duration": 91326,
                        "file_size": str(image_size),
                        "file_name": fname,
                    }
                ],
            }
        ],
        "name": name,
        "version": 1,
        "create_time": "2026-01-01 00:00:00",
        "id": file_id,
        "program_id": "com.samsung.ios.ePaper",
        "content_type": "ImageContent",
        "deploy_type": "MOBILE",
    }
