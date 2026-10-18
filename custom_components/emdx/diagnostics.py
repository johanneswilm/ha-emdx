"""Diagnostics for the Samsung EMDX integration."""
from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


def _task_stacks() -> list[dict]:
    out = []
    for t in asyncio.all_tasks():
        name = t.get_name()
        coro = t.get_coro()
        qual = getattr(coro, "__qualname__", "") or ""
        if "emdx" in name.lower() or "emdx" in qual.lower():
            frames = [
                f"{f.f_code.co_name}:{f.f_lineno}"
                for f in (t.get_stack() or [])
            ]
            out.append({"name": name, "done": t.done(), "coro": qual, "stack": frames})
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    coordinator = entry.runtime_data.get("coordinator")
    info: dict = {"entry_data_keys": sorted(entry.data)}
    if coordinator is None:
        info["coordinator"] = None
        return info

    deb = coordinator._debounced_refresh
    info["coordinator"] = {
        "data": coordinator.data,
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception),
        "update_interval": str(coordinator.update_interval),
        "listeners": len(coordinator._listeners),
        "interval_scheduled": coordinator._unsub_refresh is not None,
        "retry_after": coordinator._retry_after,
        "shutdown_requested": coordinator._shutdown_requested,
        "device_lock_locked": coordinator.lock.locked(),
        "device_lock_waiters": len(coordinator.lock._waiters or []),
        "debouncer": {
            "shutdown_requested": deb._shutdown_requested,
            "execute_lock_locked": deb._execute_lock.locked(),
            "execute_lock_waiters": len(deb._execute_lock._waiters or []),
            "timer_task": deb._timer_task is not None,
            "execute_at_end_of_timer": deb._execute_at_end_of_timer,
        },
    }
    info["emdx_tasks"] = _task_stacks()
    return info
