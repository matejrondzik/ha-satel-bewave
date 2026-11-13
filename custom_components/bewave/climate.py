"""BE WAVE thermostatic radiator heads (ART-210) as climate entities."""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
import re
import secrets

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
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

    # heating schedules (hub-wide list, edited through any head) + assigning one to a head
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "save_schedule", {vol.Optional("schedule_id", default=""): str, vol.Required("name"): str,
                          vol.Required("days"): dict}, "async_save_schedule")
    platform.async_register_entity_service(
        "delete_schedule", {vol.Required("schedule_id"): str}, "async_delete_schedule")
    platform.async_register_entity_service(
        "assign_schedule", {vol.Required("schedule_id"): str, vol.Optional("activate", default=True): bool},
        "async_assign_schedule")


_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _clean_days(days) -> dict:
    """{"0": [["08:00", "20:00"], ...], ...} (0 = Monday) -> validated, sorted, non-overlapping windows."""
    out = {}
    for k, ranges in (days or {}).items():
        day = int(k)
        if not 0 <= day <= 6:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_day", translation_placeholders={"day": str(k)})
        clean = []
        for r in ranges or []:
            a, z = (str(x).strip() for x in r)
            ma, mz = _HHMM.match(a), _HHMM.match(z)
            if not ma or not mz:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_time", translation_placeholders={"range": f"{a}–{z}"})
            ta, tz = int(ma[1]) * 60 + int(ma[2]), int(mz[1]) * 60 + int(mz[2])
            if tz <= ta:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="end_before_start", translation_placeholders={"range": f"{a}–{z}"})
            clean.append((ta, tz))
        clean.sort()
        for (a1, z1), (a2, _z2) in zip(clean, clean[1:]):
            if a2 < z1:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="overlapping_windows")
        if len(clean) > 8:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="too_many_windows")
        if clean:
            out[str(day)] = [[f"{a // 60:02d}:{a % 60:02d}", f"{z // 60:02d}:{z % 60:02d}"] for a, z in clean]
    return out


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
        # a head with an external sensor regulates by that sensor -> show its temperature (the room temperature)
        h = self._heater()
        ref = h.get("sensor_source") or h.get("sensor")
        if ref:
            for d in self._hub.devices():
                if d["id"] == ref and d.get("temp") is not None:
                    return d.get("temp")
        return self._dev().get("temp")

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
                "battery": d.get("battery"), "room": d.get("room"), "device_id": self._devid,
                "temperature_offset": h.get("offset"), "reference_sensor": h.get("sensor_source") or h.get("sensor") or None, "schedule_id": h.get("schedule") or None,
                "schedule_name": next((x["name"] for x in (self._hub.schedules or []) if x["id"] == h.get("schedule")), None),
                "schedules": self._hub.schedules}

    # ---- schedules -------------------------------------------------------------------------
    def _need_schedules(self):
        if self._hub.schedules is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="schedules_not_loaded")
        return [dict(x) for x in self._hub.schedules]

    async def async_save_schedule(self, name: str, days: dict, schedule_id: str = ""):
        cur = self._need_schedules()
        name = name.strip()[:30] or "Schedule"
        days = _clean_days(days)
        if schedule_id:
            if not any(x["id"] == schedule_id for x in cur):
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="schedule_not_found")
            cur = [({"id": x["id"], "name": name, "days": days} if x["id"] == schedule_id else x) for x in cur]
        else:
            ids = {x["id"] for x in cur}
            schedule_id = next(i for i in (secrets.token_hex(3)[:5] for _ in range(50)) if i not in ids)
            cur.append({"id": schedule_id, "name": name, "days": days})
        if not await self.hass.async_add_executor_job(self._hub.write_schedules, cur):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="hub_rejected")
        self.coordinator.async_update_listeners()

    async def async_delete_schedule(self, schedule_id: str):
        cur = self._need_schedules()
        users = [d.get("name") or d["id"] for d in self._hub.devices() if (d.get("heater") or {}).get("schedule") == schedule_id]
        if users:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="schedule_in_use", translation_placeholders={"heads": ", ".join(map(str, users))})
        if not await self.hass.async_add_executor_job(self._hub.write_schedules, [x for x in cur if x["id"] != schedule_id]):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="hub_rejected")
        self.coordinator.async_update_listeners()

    async def async_assign_schedule(self, schedule_id: str, activate: bool = True):
        if schedule_id and not any(x["id"] == schedule_id for x in self._need_schedules()):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="schedule_not_found")
        await self.hass.async_add_executor_job(self._hub.assign_schedule, self._devid, schedule_id, activate)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = round(float(temp) * 10) / 10       # heads take 0.1 °C (UI steps stay 0.5)
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
