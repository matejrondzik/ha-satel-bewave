"""BE WAVE light: the built-in light ring of SLIM LUNA detectors."""
from __future__ import annotations

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
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
            if d.get("hwtype") in proto.LUNA_TYPES and d["id"] not in known:
                known.add(d["id"])
                new.append(BeWaveLunaLight(coordinator, hub, entry, d["id"]))
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BeWaveLunaLight(CoordinatorEntity, LightEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "luna_light"
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid: int):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_light"
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
    def is_on(self) -> bool | None:
        return self._dev().get("on_off")

    async def _set(self, on: bool):
        await self.hass.async_add_executor_job(self._hub.set_on_off, self._devid, on)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
