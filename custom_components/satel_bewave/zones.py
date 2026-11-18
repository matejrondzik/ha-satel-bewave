"""Wired zone inputs of the panel (Z1, Z2, …): what is connected to them.

The protocol only reports violated / not violated, so the user picks the device class once - in the
options flow or through the repair issue raised after setup. Stored in ``entry.options["zone_classes"]``
as ``{"<device id>": "<binary sensor device class>"}``; "none" = plain on/off.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from . import bewave_client as proto
from .const import CONF_ZONE_CLASSES

ZONE_OPTION = 110          # HubDevice option present only on wired zone inputs
ZONE_CLASSES = ["none", "smoke", "door", "garage_door", "window", "opening", "motion", "moisture", "gas",
                "heat", "carbon_monoxide", "vibration", "tamper", "problem", "safety"]


def is_zone(d: dict) -> bool:
    return ZONE_OPTION in (d.get("options") or [])


def zone_label(d: dict) -> str:
    """'Z2 · Garage door (Garage)' - Z number from the composite id (uuid*1000 + index)."""
    did = d["id"]
    idx = proto.COMPOSITE.get(did, (None, None))[1] if isinstance(did, int) else None
    z = f"Z{idx}" if idx else f"#{did}"
    name = d.get("name") or ""
    room = d.get("room") or ""
    return " · ".join(x for x in (z, name + (f" ({room})" if room else "")) if x)


def zones(hub) -> list[dict]:
    return sorted((d for d in hub.devices() if is_zone(d)), key=lambda d: d["id"])


def schema(hub, current: dict) -> tuple[vol.Schema, dict[str, int]]:
    """Form with one select per zone. Returns the schema and label -> device id."""
    fields, keys = {}, {}
    for d in zones(hub):
        label = zone_label(d)
        keys[label] = d["id"]
        fields[vol.Required(label, default=current.get(str(d["id"]), "none"))] = SelectSelector(
            SelectSelectorConfig(options=ZONE_CLASSES, mode=SelectSelectorMode.DROPDOWN,
                                 translation_key="zone_class"))
    return vol.Schema(fields), keys


def merge(options: dict, user_input: dict, keys: dict[str, int]) -> dict:
    classes = dict(options.get(CONF_ZONE_CLASSES) or {})
    for label, did in keys.items():
        classes[str(did)] = user_input.get(label, "none")
    return {**options, CONF_ZONE_CLASSES: classes}


def current_classes(hass, entry, hub) -> dict:
    """Stored choices, falling back to a device class the user set on the entity itself."""
    from homeassistant.helpers import entity_registry as er
    from .const import DOMAIN
    cur = dict(entry.options.get(CONF_ZONE_CLASSES) or {})
    reg = er.async_get(hass)
    serial = entry.data.get("serial") or entry.entry_id
    for d in zones(hub):
        if str(d["id"]) in cur:
            continue
        eid = reg.async_get_entity_id("binary_sensor", DOMAIN, f"bewave_{serial}_{d['id']}_state")
        ent = reg.async_get(eid) if eid else None
        if ent and ent.device_class in ZONE_CLASSES:
            cur[str(d["id"])] = ent.device_class
    return cur


def clear_overrides(hass, entry, hub) -> None:
    """After the user chose zone types here, drop device classes set on the entities by hand - otherwise
    they would win over the new choice."""
    from homeassistant.helpers import entity_registry as er
    from .const import DOMAIN
    reg = er.async_get(hass)
    serial = entry.data.get("serial") or entry.entry_id
    for d in zones(hub):
        eid = reg.async_get_entity_id("binary_sensor", DOMAIN, f"bewave_{serial}_{d['id']}_state")
        ent = reg.async_get(eid) if eid else None
        if ent and ent.device_class:
            reg.async_update_entity(eid, device_class=None)


def device_class(options: dict, devid) -> str | None:
    c = (options.get(CONF_ZONE_CLASSES) or {}).get(str(devid))
    return None if c in (None, "none") else c
