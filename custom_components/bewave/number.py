"""BE WAVE numbers: optical signaling period (light-on time) of SLIM LUNA detectors."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
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
