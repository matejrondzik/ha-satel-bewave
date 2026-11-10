"""BE WAVE sensors: temperature and battery level per device."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity, SensorDeviceClass, SensorStateClass)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

# kind -> (field, translation_key or None, device_class, unit, icon)
_KINDS = {
    "temp": ("temp", None, SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, None),
    "battery": ("battery", None, SensorDeviceClass.BATTERY, PERCENTAGE, None),
    # thermostat heads: how far the valve is open (0 % = closed)
    "valve": ("valve", "valve_opening", None, PERCENTAGE, "mdi:valve"),
}


def _value(d: dict, kind: str):
    if kind == "valve":
        return (d.get("heater") or {}).get("valve")
    return d.get(_KINDS[kind][0])


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    hub, coordinator = data["hub"], data["coordinator"]
    known: set[tuple[int, str]] = set()

    @callback
    def _sync():
        new = []
        for d in hub.devices():
            for kind in _KINDS:
                if _value(d, kind) is not None and (d["id"], kind) not in known:
                    known.add((d["id"], kind))
                    new.append(BeWaveSensor(coordinator, hub, entry, d["id"], kind))
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BeWaveSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid, kind):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        self._kind = kind
        _f, tkey, dclass, unit, icon = _KINDS[kind]
        if tkey:
            self._attr_translation_key = tkey
        if icon:
            self._attr_icon = icon
        self._attr_device_class = dclass
        self._attr_native_unit_of_measurement = unit
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_{kind}"
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
    def native_value(self):
        return _value(self._dev(), self._kind)
