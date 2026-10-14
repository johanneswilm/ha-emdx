# Samsung EMDX e-paper display integration for Home Assistant

Controls Samsung EMDX colour e-paper panels (EM32DX and friends) over the
local network using the Samsung MDC protocol — no Samsung app, no cloud.

## What works (verified on EM32DX, fw S-RSEDWWC-1050.1)

- **Push images** — `emdx.push_image` from a file path, URL, or camera entity.
  The image is resized to the panel's native 2560x1440 and served to the
  display over a small built-in HTTP server; the panel fetches and renders it.
- **Battery sensor** (level, charging state).
- **Network standby switch** (MDC 0xB5) — keep the WiFi listener alive while
  sleeping (required for Wake-on-LAN) or turn it off for true deep sleep.
- **Deep sleep switch** — network standby off + power off; turning it off
  sends a Wake-on-LAN magic packet and powers the panel back up.
- **Panel power switch** (optimistic; the panel never reports power state).
- **Firmware sensor** (disabled by default).

## Setup

1. Put the display on your WiFi (the Samsung app or its settings menu), note
   its IP and the PIN shown on the display.
2. Add the integration (Settings → Devices & Services → Add Integration →
   "Samsung EMDX e-paper"), enter host + PIN, and the MAC address if you want
   Wake-on-LAN.
3. Push an image — easiest via the bundled dashboard card: add a
   `custom:emdx-push-card` card with an `entity:` of your panel switch; click
   it, pick an image, and it is downscaled in the browser, uploaded to
   `/api/emdx/upload` and pushed. Each upload replaces the previous one. The
   card's JavaScript resource (`/emdx/card.js`) is registered automatically
   when the integration loads — it then works in desktop browsers and in the
   mobile app. Only if you manage Lovelace resources in YAML
   (`resource_mode: yaml`) must you add the module resource yourself.
   Or call the service directly:

```yaml
service: emdx.push_image
data:
  image: /media/dashboard.jpg
  sleep_after: 2   # optional: deep-sleep the panel 2 min after the push
target:
  entity_id: switch.samsung_emdx_1234_panel_power
```

## Notes / protocol

- Transport: TCP 1515, `MDCSTART<<TLS>>` → TLS (self-signed) → PIN auth.
- The display serves **one** MDC session at a time and keeps a dead session
  reserved for ~30–60 s; the integration retries connects automatically.
- In deep sleep with network standby off the panel is completely invisible on
  the network — entities go unavailable; use `emdx.wake` (needs MAC).
- Content is served by HA itself on port 8123 under
  `/emdx/c/<token>/content.json` + `/image` (one push per token). The newest
  push stays fetchable indefinitely — the display re-fetches its content URL
  later, and a 404 makes it show "Configuration error".
