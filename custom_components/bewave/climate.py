"""BE WAVE thermostatic radiator heads (ART-210) as climate entities."""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bewave_client as proto
from .const import DOMAIN

PRESET_BOOST = "boost"
MODE_TO_PRESET = proto.HEATER_MODES                     # 0 manual, 1 schedule, 2 eco
PRESET_TO_MODE = {v: k for k, v in MODE_TO_PRESET.items()}
PRESETS = [*MODE_TO_PRESET.values(), PRESET_BOOST]


def heater_of(hub, devid) -> dict:
    for d in hub.devices():
        if d["id"] == devid:
            return d.get("heater") or {}
    return {}


def preset_of(heater: dict) -> str | None:
    if heater.get("boost"):
        return PRESET_BOOST
    return MODE_TO_PRESET.get(heater.get("mode"))


def apply_preset(hub, devid, preset: str) -> None:
    """Blocking: switch one head to a preset (shared by the thermostat and the house-wide select)."""
    h = lambda: heater_of(hub, devid)
    if preset == PRESET_BOOST:
        hub.set_patch(devid, proto.SET_HEATER_BOOST, boolean=True, verify=lambda: h().get("boost") is True)
        return
    mode = PRESET_TO_MODE[preset]
    if h().get("boost"):
        hub.set_patch(devid, proto.SET_HEATER_BOOST, boolean=False, verify=lambda: h().get("boost") is False)
    hub.set_patch(devid, proto.SET_HEATER_WORKING_MODE, number=mode, verify=lambda: h().get("mode") == mode)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    hub, coordinator = data["hub"], data["coordinator"]
    known: set[int] = set()

    @callback
    def _sync():
        new = []
        for d in hub.devices():
            if d.get("hwtype") == proto.ART_210 and d["id"] not in known:
                known.add(d["id"])
                new.append(BeWaveThermostat(coordinator, hub, entry, d["id"]))
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BeWaveThermostat(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5
    _attr_preset_modes = PRESETS
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE
                                | ClimateEntityFeature.PRESET_MODE)

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid: int):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_climate"
        d = self._dev()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial}_{devid}")},
            manufacturer="Satel", model="ART-210",
            name=d.get("name") or f"BE WAVE {devid}",
        )

    def _dev(self):
        for d in self._hub.devices():
            if d["id"] == self._devid:
                return d
        return {}

    def _heater(self):
        return self._dev().get("heater") or {}

    @property
    def current_temperature(self):
        own = self._dev().get("temp")
        if own is not None:
            return own
        ref = self._heater().get("sensor")       # head regulates by an external sensor
        for d in self._hub.devices():
            if ref and d["id"] == ref:
                return d.get("temp")
        return None

    @property
    def target_temperature(self):
        return self._heater().get("target")

    @property
    def hvac_action(self):
        valve = self._heater().get("valve")
        if valve is None:
            return None
        return HVACAction.HEATING if valve > 0 else HVACAction.IDLE

    @property
    def preset_mode(self):
        return preset_of(self._heater())

    @property
    def extra_state_attributes(self):
        h = self._heater(); d = self._dev()
        return {"valve_opening": h.get("valve"), "temperature_day": h.get("day"),
                "temperature_night": h.get("night"), "temperature_eco": h.get("eco"),
                "temperature_manual": h.get("manual"), "child_lock": h.get("parental"),
                "boost_time_s": (h.get("boost_time") or 0) // 1000 or None,
                "battery": d.get("battery"), "room": d.get("room"), "device_id": self._devid}

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = round(float(temp) * 2) / 2
        await self.hass.async_add_executor_job(
            lambda: self._hub.set_patch(
                self._devid, proto.SET_HEATER_FORCED_VALUE, flt=temp,
                verify=lambda: abs((self._heater().get("target") or -99) - temp) < 0.05))
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str):
        await self.hass.async_add_executor_job(apply_preset, self._hub, self._devid, preset_mode)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        return  # ART-210 only heats
