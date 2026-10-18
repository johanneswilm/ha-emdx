"""Samsung MDC (Multiple Display Control) client for EMDX e-paper displays.

Protocol (reverse-engineered, matches node-samsung-emdx / WeeJeWel node-samsung-mdc):
  TCP :1515 -> display sends "MDCSTART<<TLS>>" -> TLS upgrade (self-signed cert)
  -> client writes PIN as ASCII -> "MDCAUTH<<PASS>>" or "MDCAUTH<<FAIL:0xNN>>".
  Commands: 0xAA cmd displayId len payload... checksum (sum of cmd..payload mod 256).
  Replies:  0xAA displayId len ACK/NAK cmd payload... checksum.
"""
from __future__ import annotations

import asyncio
import logging
import ssl

_logger = logging.getLogger(__name__)

# Built at import time: load_default_certs() is a blocking call and must not
# run on the event loop (HA 2026 detects it).
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

HEADER = 0xAA
ACK = 0x41
NAK = 0x4E

# Command ids confirmed on EM32DX (firmware S-RSEDWWC-1050.1)
CMD_SERIAL = 0x0B
CMD_SW_VERSION = 0x0E
CMD_MODEL_INFO = 0x10
CMD_STATUS = 0x1B          # sub-commands: 0x73 battery, 0x87 PIN
CMD_POWER = 0x11           # set only; get is NAK'd
CMD_ON_TIMER = 0x25
CMD_DEVICE_NAME = 0x67
CMD_CLOCK = 0xA7
CMD_NETWORK_STANDBY = 0xB5
CMD_CONTENT_DOWNLOAD = 0xC7


class MdcError(Exception):
    """Base MDC protocol error."""


class MdcAuthError(MdcError):
    """PIN rejected or display blocked after failed attempts."""


class MdcConnectionError(MdcError):
    """Display unreachable, asleep, or session busy."""


def _frame(cmd: int, data: bytes = b"", display_id: int = 0x00) -> bytes:
    payload = bytes([cmd, display_id, len(data)]) + data
    return bytes([HEADER]) + payload + bytes([sum(payload) % 256])


class MdcDevice:
    """One MDC session. The display serves a single connection at a time."""

    def __init__(self, host: str, pin: str, port: int = 1515) -> None:
        self.host = host
        self.pin = pin
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buf = bytearray()

    async def connect(self, attempts: int = 4, retry_delay: float = 8.0) -> None:
        """Connect, retrying: the display keeps a dead session reserved for
        ~30-60 s and refuses new TCP connections meanwhile."""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                await self._connect_once()
                return
            except MdcConnectionError as err:
                last = err
                if attempt < attempts - 1:
                    await asyncio.sleep(retry_delay)
        raise last if last else MdcConnectionError("connect failed")

    async def _connect_once(self) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=10
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise MdcConnectionError(f"cannot reach {self.host}:{self.port}: {err}") from err
        try:
            greeting = await asyncio.wait_for(reader.readexactly(15), timeout=10)
            if greeting != b"MDCSTART<<TLS>>":
                raise MdcConnectionError(f"unexpected greeting: {greeting!r}")

            ctx = _SSL_CONTEXT
            # Upgrade the same socket: the display serves a single session and
            # a second TCP connection would be refused/ignored.
            await writer.start_tls(ctx)
            self._reader, self._writer = reader, writer

            writer.write(self.pin.encode())
            await writer.drain()
            # The verdict arrives as a single record; read(64) cannot overrun.
            auth = await asyncio.wait_for(reader.read(64), timeout=10)
            if auth == b"MDCAUTH<<PASS>>":
                return
            if auth.startswith(b"MDCAUTH<<FAIL"):
                raise MdcAuthError(f"authentication failed: {auth!r}")
            raise MdcConnectionError(f"unexpected auth response: {auth!r}")
        except (OSError, ssl.SSLError, asyncio.TimeoutError) as err:
            await self._safe_close()
            raise MdcConnectionError(f"TLS/auth handshake failed: {err}") from err

    async def _safe_close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
        self._reader = self._writer = None
        self._buf = bytearray()

    async def disconnect(self) -> None:
        await self._safe_close()

    @staticmethod
    def _checksum_ok(frame: bytes) -> bool:
        # Verified on EM32DX: checksum = sum(displayId..payload) mod 256.
        return frame[-1] == sum(frame[1:-1]) % 256

    async def _read_response(self, expect_cmd: int | None = None) -> bytes:
        """Read a response frame, resyncing over unsolicited/garbage bytes.

        The EM32DX `len` byte is inconsistent across commands (it equals
        total-frame-size minus 4 for some replies, minus 5 for others), so
        each candidate body length is validated by checksum before accept.
        """
        buf = self._buf
        while True:
            while HEADER not in buf:
                chunk = await self._reader.read(256)
                if not chunk:
                    raise MdcConnectionError("connection closed by display")
                buf += chunk
            del buf[: buf.index(HEADER)]
            if len(buf) < 6:
                chunk = await self._reader.read(256)
                if not chunk:
                    raise MdcConnectionError("connection closed by display")
                buf += chunk
                continue
            length = buf[3]
            accepted = False
            for extra in (0, 1, -1):
                need = 5 + length + extra  # header + displayId len ack cmd + payload+chk
                if need < 7:
                    continue
                while len(buf) < need:
                    chunk = await self._reader.read(256)
                    if not chunk:
                        raise MdcConnectionError("connection closed by display")
                    buf += chunk
                frame = bytes(buf[:need])
                if self._checksum_ok(frame):
                    if expect_cmd is not None and frame[5] != expect_cmd:
                        _logger.debug("unsolicited frame 0x%02X: %r", frame[5], frame)
                        del buf[:need]
                        accepted = True
                        break
                    del buf[:need]
                    return frame
            if not accepted:
                del buf[:1]  # false header byte; resync one byte forward

    async def command(self, cmd: int, data: bytes = b"") -> bytes:
        """Send a command, return the ACK payload. Raises on NAK/timeout."""
        if self._writer is None:
            raise MdcError("not connected")
        # The display throttles after ~30-60 rapid commands; keep cadence gentle.
        await asyncio.sleep(0.3)
        self._writer.write(_frame(cmd, data))
        await self._writer.drain()
        try:
            frame = await asyncio.wait_for(self._read_response(expect_cmd=cmd), timeout=8)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, OSError) as err:
            raise MdcConnectionError(f"no response to 0x{cmd:02X}: {err}") from err
        ack, payload = frame[4], frame[6:-1]
        if ack == NAK:
            raise MdcError(f"NAK for 0x{cmd:02X}: {payload!r}")
        if ack != ACK:
            raise MdcError(f"unexpected response to 0x{cmd:02X}: {frame!r}")
        return payload

    # ---- high-level operations ----

    async def get_serial(self) -> str:
        return (await self.command(CMD_SERIAL)).decode(errors="replace")

    async def get_sw_version(self) -> str:
        return (await self.command(CMD_SW_VERSION)).decode(errors="replace")

    async def get_device_name(self) -> str:
        return (await self.command(CMD_DEVICE_NAME)).decode(errors="replace")

    async def get_network_standby(self) -> bool:
        r = await self.command(CMD_NETWORK_STANDBY)
        if not r:
            raise MdcError("empty network standby response")
        return r[0] == 0x01

    async def set_network_standby(self, enabled: bool) -> None:
        await self.command(CMD_NETWORK_STANDBY, bytes([0x01 if enabled else 0x00]))

    async def set_power(self, on: bool) -> None:
        await self.command(CMD_POWER, bytes([0x01 if on else 0x00]))

    async def get_battery(self) -> dict:
        r = await self.command(CMD_STATUS, bytes([0x73]))
        # r = [0x73, charging, present, healthy, level, ...]
        if len(r) < 5 or r[0] != 0x73:
            raise MdcError(f"unexpected battery response: {r!r}")
        return {
            "charging": r[1] == 0x01,
            "present": r[2] == 0x01,
            "healthy": r[3] == 0x01,
            "level": r[4],
        }

    async def set_content_download(self, url: str) -> None:
        if len(url) > 255:
            raise MdcError("content URL exceeds 255 characters")
        await self.command(
            CMD_CONTENT_DOWNLOAD, bytes([0x53, 0x80, len(url)]) + url.encode()
        )


def wol_magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    return b"\xff" * 6 + raw * 16


async def send_wol(mac: str, target_ip: str | None = None) -> None:
    """Magic packet to the limited broadcast and, when known, the /24 directed
    broadcast: HAOS/wired hosts' 255.255.255.255 may not reach WLAN clients."""
    packet = wol_magic_packet(mac)
    loop = asyncio.get_running_loop()
    addrs = ["255.255.255.255"]
    if target_ip:
        parts = target_ip.split(".")
        if len(parts) == 4:
            directed = ".".join(parts[:3]) + ".255"
            if directed != "255.255.255.255":
                addrs.append(directed)
    for addr in addrs:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            remote_addr=(addr, 9),
            allow_broadcast=True,
        )
        try:
            for _ in range(3):
                transport.sendto(packet)
                await asyncio.sleep(0.2)
        finally:
            transport.close()
