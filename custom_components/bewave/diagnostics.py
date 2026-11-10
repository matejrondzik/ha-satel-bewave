"""Diagnostics for BE WAVE (Settings → Devices & services → BE WAVE → ⋮ → Download diagnostics).

Credentials, the device token, the hub serial and user-given names are redacted so the
file can be attached to a GitHub issue safely.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_UUID, CONF_LOGIN, CONF_PASSWORD, CONF_SERIAL, DOMAIN

TO_REDACT = {CONF_PASSWORD, CONF_LOGIN, CONF_DEVICE_UUID, CONF_SERIAL, "name", "room"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    hub = data.get("hub")
    coordinator = data.get("coordinator")
    hub_info: dict[str, Any] = {}
    if hub is not None:
        devices = []
        for i, d in enumerate(hub.devices()):
            dd = dict(d)
            dd["id"] = f"device_{i}"           # hub device ids are stable per installation
            devices.append(dd)
        hub_info = {
            "alarm_state": hub.state,
            "settings_flags": hub.flags,
            "hub_info": hub.info,
            "connected": hub.conn is not None,
            "protocol_ids": {"user_id": hub.client.user_id, "device_id": hub.client.device_id},
            "devices": devices,
        }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "hub": async_redact_data(hub_info, TO_REDACT),
    }
