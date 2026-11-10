"""BE WAVE binary sensors: detectors, plus tamper / trouble of the hub and of every device."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity, BinarySensorDeviceClass)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bewave_client as proto
from .errors import describe
from .const import DOMAIN

_CLASS = {"contact": BinarySensorDeviceClass.OPENING,
          "motion": BinarySensorDeviceClass.MOTION}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    hub, coordinator = data["hub"], data["coordinator"]
    known: set = set()
    async_add_entities([BeWaveHubTrouble(coordinator, hub, entry, tamper=True),
                        BeWaveHubTrouble(coordinator, hub, entry, tamper=False)])

    @callback
    def _sync():
        new = []
        for d in hub.devices():
            if d["cat"] in _CLASS and d["id"] not in known:
                known.add(d["id"])
                new.append(BeWaveBinary(coordinator, hub, entry, d["id"], _CLASS[d["cat"]]))
            if d.get("hwtype") in proto.KEYPAD_TYPES and ("keypad", d["id"]) not in known:
                known.add(("keypad", d["id"]))
                new.append(BeWaveKeypadBlock(coordinator, hub, entry, d["id"]))
            if d.get("errors") is not None and d.get("hwtype") != proto.VIRTUAL_IP_DEVICE \
                    and ("trouble", d["id"]) not in known:
                known.add(("trouble", d["id"]))
                new += [BeWaveDeviceTrouble(coordinator, hub, entry, d["id"], tamper=True),
                        BeWaveDeviceTrouble(coordinator, hub, entry, d["id"], tamper=False)]
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BeWaveBinary(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid, devclass):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        self._attr_device_class = devclass
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_state"
        d = self._dev()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial}_{devid}")},
            manufacturer="Satel", model=d.get("model"),
            name=d.get("name") or f"BE WAVE {devid}",
        )

    def _dev(self):
        for d in self._hub.devices():
            if d["id"] == self._devid:
                return d
        return {}

    @property
    def is_on(self):
        s = self._dev().get("state")
        return None if s is None else (s == 1)

    @property
    def extra_state_attributes(self):
        d = self._dev()
        return {"room": d.get("room"), "system_number": d.get("sysnum"),
                "serial": d.get("id"), "signal_pct": d.get("signal"),
                "battery_pct": d.get("battery"), "battery_voltage": d.get("volt"),
                "bypassed": d.get("bypass")}


class BeWaveHubTrouble(CoordinatorEntity, BinarySensorEntity):
    """Hub tamper (cover / wall) or any hub trouble from SystemInfo error codes."""
    _attr_has_entity_name = True

    def __init__(self, coordinator, hub, entry: ConfigEntry, tamper: bool):
        super().__init__(coordinator)
        self._hub = hub
        self._tamper = tamper
        serial = entry.data.get("serial") or entry.entry_id
        key = "hub_tamper" if tamper else "hub_problem"
        self._attr_translation_key = key
        self._attr_unique_id = f"bewave_{serial}_{key}"
        self._attr_device_class = BinarySensorDeviceClass.TAMPER if tamper else BinarySensorDeviceClass.PROBLEM
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)}, manufacturer="Satel",
            model="BE WAVE Smart HUB", name=f"BE WAVE {serial}")

    @property
    def is_on(self):
        errs = self._hub.info.get("errors")
        if errs is None:
            return None
        return proto.HUB_TAMPER in errs if self._tamper else bool(errs)

    @property
    def extra_state_attributes(self):
        if self._tamper:
            return None
        errs = self._hub.info.get("errors") or []
        mem = self._hub.info.get("errors_memory") or []
        lang = self.hass.config.language if self.hass else None
        keys = [proto.HUB_ERRORS.get(c, f"code_{c}") for c in errs]
        return {"problems": ", ".join(describe(keys, lang)) or None, "problem_keys": keys, "codes": errs,
                "memory": ", ".join(describe([proto.HUB_ERRORS.get(c, f"code_{c}") for c in mem], lang)) or None}


class BeWaveDeviceTrouble(CoordinatorEntity, BinarySensorEntity):
    """Per-device tamper or trouble (HubDeviceState error codes)."""
    _attr_has_entity_name = True

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid, tamper: bool):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        self._tamper = tamper
        serial = entry.data.get("serial") or entry.entry_id
        key = "tamper" if tamper else "problem"
        self._attr_translation_key = key
        self._attr_unique_id = f"bewave_{serial}_{devid}_{key}"
        self._attr_device_class = BinarySensorDeviceClass.TAMPER if tamper else BinarySensorDeviceClass.PROBLEM
        if not tamper:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        d = self._dev()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial}_{devid}")},
            manufacturer="Satel", model=d.get("model"),
            name=d.get("name") or f"BE WAVE {devid}")

    def _dev(self):
        for d in self._hub.devices():
            if d["id"] == self._devid:
                return d
        return {}

    @property
    def is_on(self):
        errs = self._dev().get("errors")
        if errs is None:
            return None
        return proto.DEV_TAMPER in errs if self._tamper else bool(errs)

    @property
    def extra_state_attributes(self):
        if self._tamper:
            return None
        errs = self._dev().get("errors") or []
        lang = self.hass.config.language if self.hass else None
        keys = [proto.DEV_ERRORS.get(c, f"code_{c}") for c in errs]
        return {"problems": ", ".join(describe(keys, lang)) or None, "problem_keys": keys, "codes": errs}


class BeWaveKeypadBlock(BeWaveDeviceTrouble):
    """Keypad locked by the hub (e.g. after too many wrong codes)."""

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid):
        super().__init__(coordinator, hub, entry, devid, tamper=False)
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_translation_key = "keypad_blocked"
        self._attr_unique_id = f"bewave_{serial}_{devid}_keypad_blocked"
        self._attr_device_class = None
        self._attr_entity_category = None

    @property
    def is_on(self):
        v = self._dev().get("keypad_block")
        return None if v is None else bool(v)

    @property
    def icon(self):
        return "mdi:dialpad" if not self.is_on else "mdi:lock-alert"

    @property
    def extra_state_attributes(self):
        d = self._dev()
        return {"model": d.get("model"), "system_number": d.get("sysnum"), "supply_voltage": d.get("volt"),
                "signal_pct": d.get("signal")}
