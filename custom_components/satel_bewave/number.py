"""BE WAVE numbers: optical signaling period of SLIM LUNA detectors; preset temperatures, offset and boost time of ART-210 heads."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bewave_client as proto
from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    hub, coordinator = data["hub"], data["coordinator"]
    known: set[int] = set()

    @callback
    def _sync():
        new = []
        for d in hub.devices():
            if proto.SET_ALARM_OPTICAL_DURATION in (d.get("options") or []) and d["id"] not in known:
                known.add(d["id"])
                new.append(BeWaveOpticalDuration(coordinator, hub, entry, d["id"]))
            # detectors list command 66 in their options; LUNA bus detectors do not, but carry the field in their state
            has_off = (proto.SET_HEATER_OFFSET in (d.get("options") or [])
                       or (d.get("temp") is not None and proto.SET_HEATER_OFFSET in (d.get("raw") or {})))
            if has_off and d.get("hwtype") != proto.ART_210 and ("o", d["id"]) not in known:
                known.add(("o", d["id"]))
                new.append(BeWaveSensorOffset(coordinator, hub, entry, d["id"]))
            if d.get("hwtype") == proto.ART_210 and ("h", d["id"]) not in known:
                known.add(("h", d["id"]))
                new += [BeWaveHeaterNumber(coordinator, hub, entry, d["id"], spec) for spec in HEATER_NUMBERS]
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BeWaveOpticalDuration(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "optical_duration"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 0
    _attr_native_max_value = 180
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid: int):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_optical_duration"
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

    def _current(self):
        raw = self._dev().get("raw")
        # proto3 omits zero values -> missing field means 0
        return None if raw is None else raw.get(proto.ALARM_OPTICAL_DURATION_FIELD, 0)

    @property
    def native_value(self):
        return self._current()

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        await self.hass.async_add_executor_job(
            lambda: self._hub.set_patch(self._devid, proto.SET_ALARM_OPTICAL_DURATION,
                                        verify=lambda: self._current() == v, number=v))
        self.async_write_ha_state()


# key in the parsed heater dict, command, translation key, min, max, step, unit, scale (HA value -> hub value)
HEATER_NUMBERS = [
    ("day", proto.SET_HEATER_TEMP_DAY, "temperature_day", 5, 30, 0.5, UnitOfTemperature.CELSIUS, None),
    ("night", proto.SET_HEATER_TEMP_NIGHT, "temperature_night", 5, 30, 0.5, UnitOfTemperature.CELSIUS, None),
    ("eco", proto.SET_HEATER_TEMP_ECO, "temperature_eco", 5, 30, 0.5, UnitOfTemperature.CELSIUS, None),
    ("manual", proto.SET_HEATER_TEMP_MANUAL, "temperature_manual", 5, 30, 0.5, UnitOfTemperature.CELSIUS, None),
    ("offset", proto.SET_HEATER_OFFSET, "temperature_offset", -5, 5, 0.1, UnitOfTemperature.CELSIUS, None),
    ("boost_time", proto.SET_HEATER_BOOST_TIME, "boost_time", 1, 120, 1, UnitOfTime.MINUTES, 60000),
]


class BeWaveHeaterNumber(CoordinatorEntity, NumberEntity):
    """One ART-210 setting (preset temperature, offset, boost time) - same patches as the Be Wave app."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid: int, spec):
        super().__init__(coordinator)
        self._hub, self._devid = hub, devid
        (self._key, self._cmd, self._attr_translation_key, self._attr_native_min_value, self._attr_native_max_value,
         self._attr_native_step, self._attr_native_unit_of_measurement, self._scale) = spec
        if self._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_heater_{self._key}"
        d = self._dev()
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{serial}_{devid}")},
                                            manufacturer="Satel", model=d.get("model"),
                                            name=d.get("name") or f"BE WAVE {devid}")

    def _dev(self):
        for d in self._hub.devices():
            if d["id"] == self._devid:
                return d
        return {}

    def _current(self):
        v = (self._dev().get("heater") or {}).get(self._key)
        if v is None:
            return None
        return round(v / self._scale) if self._scale else round(v, 2)

    @property
    def native_value(self):
        return self._current()

    async def async_set_native_value(self, value: float) -> None:
        if self._scale:
            want, kw = round(value), {"number": int(round(value) * self._scale)}
        else:
            want = round(value * 10) / 10
            kw = {"flt": want}
        await self.hass.async_add_executor_job(
            lambda: self._hub.set_patch(self._devid, self._cmd,
                                        verify=lambda: self._current() is not None and abs(self._current() - want) < 0.05, **kw))
        self.async_write_ha_state()


class BeWaveSensorOffset(BeWaveHeaterNumber):
    """Temperature correction of a detector (APD-200 / APMD-250 / AFD-200): command 66 Float, same as on a head.
    A head with an external sensor regulates by this corrected temperature."""

    def __init__(self, coordinator, hub, entry, devid):
        super().__init__(coordinator, hub, entry, devid,
                         ("offset", proto.SET_HEATER_OFFSET, "temperature_offset", -5, 5, 0.1, UnitOfTemperature.CELSIUS, None))

    def _current(self):
        raw = self._dev().get("raw")
        if raw is None:
            return None
        v = raw.get(proto.SET_HEATER_OFFSET, 0)      # proto3 omits 0
        return round(proto._f32(v), 2) if isinstance(v, int) and v else 0.0
