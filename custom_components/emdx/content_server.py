"""Tiny HTTP server the EMDX pulls content.json + image from.

The display only speaks the "content download" flow: MDC 0xC7 hands it a URL
to a manifest, then it GETs the manifest and the image it references. We keep
exactly one pending push per config entry, guarded by a random token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time

from aiohttp import web

_LOGGER = logging.getLogger(__name__)

CONTENT_TTL = 600  # seconds a pending push stays fetchable


class ContentServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.token = secrets.token_urlsafe(16)
        self._runner: web.AppRunner | None = None
        self._pending: dict | None = None
        self._fetched = asyncio.Event()
        self._loop = asyncio.get_running_loop()

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/c/{token}/content.json", self._handle_manifest)
        app.router.add_get("/c/{token}/image", self._handle_image)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        _LOGGER.debug("EMDX content server listening on :%d", self.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def stage(self, image: bytes, host: str) -> tuple[str, str]:
        """Stage one push; returns (content_url, image_url) sharing one token."""
        self.token = secrets.token_urlsafe(16)
        base = f"http://{host}:{self.port}/c/{self.token}"
        self._pending = {
            "manifest": None,
            "image": image,
            "expires": time.monotonic() + CONTENT_TTL,
        }
        self._fetched.clear()
        return f"{base}/content.json", f"{base}/image"

    def commit(self, manifest: dict) -> None:
        """Attach the manifest that references the staged image URL."""
        if self._pending is not None:
            self._pending["manifest"] = manifest

    async def wait_fetched(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._fetched.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _valid(self, token: str) -> bool:
        return (
            self._pending is not None
            and token == self.token
            and self._pending["expires"] > time.monotonic()
        )

    async def _handle_manifest(self, request: web.Request) -> web.Response:
        if not self._valid(request.match_info["token"]) or self._pending["manifest"] is None:
            return web.Response(status=403)
        body = json.dumps(self._pending["manifest"]).replace("/", "\\/")
        _LOGGER.debug("EMDX fetched content.json")
        return web.Response(text=body, content_type="application/json")

    async def _handle_image(self, request: web.Request) -> web.Response:
        if not self._valid(request.match_info["token"]):
            return web.Response(status=403)
        _LOGGER.debug("EMDX fetched image (%d bytes)", len(self._pending["image"]))
        self._loop.call_soon(self._fetched.set)
        return web.Response(
            body=self._pending["image"], content_type="image/jpeg"
        )


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
