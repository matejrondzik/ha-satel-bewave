"""BE WAVE site-setting switches (LED indicator, GRADE 2, SATEL server)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bewave_client as proto
from .const import DOMAIN

# key -> (display name, reliability note)
SWITCHES = [
    ("led", "LED indicator"),
    ("grade2", "Grade 2"),
    ("satel", "SATEL server connection"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    hub, coordinator = data["hub"], data["coordinator"]
    async_add_entities(
        BeWaveSettingSwitch(coordinator, hub, entry, key, name)
        for key, name in SWITCHES
    )
    known: set[int] = set()

    @callback
    def _sync():
        new = []
        for d in hub.devices():
            # virtual IP devices + any other output the hub lets us switch (options contain SET_ON_OFF),
            # e.g. a hub main-board output O1 set up as a bistable switch; LUNA detectors are lights instead
            # hwtype comes with the config (telemetry can arrive first) - decide only once the type is known,
            # otherwise LUNA detectors (their light ring has its own light entity) would get a switch too
            switchable = (d.get("hwtype") is not None and proto.SET_ON_OFF in (d.get("options") or [])
                          and d.get("hwtype") not in proto.LUNA_TYPES)
            if (d.get("hwtype") == proto.VIRTUAL_IP_DEVICE or switchable) and d["id"] not in known:
                known.add(d["id"])
                new.append(BeWaveDeviceSwitch(coordinator, hub, entry, d["id"]))
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class BeWaveDeviceSwitch(CoordinatorEntity, SwitchEntity):
    """On/off output of a BE WAVE device (virtual IP devices, hub outputs)."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator, hub, entry: ConfigEntry, devid: int):
        super().__init__(coordinator)
        self._hub = hub
        self._devid = devid
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{devid}_onoff"
        d = self._dev()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial}_{devid}")},
            manufacturer="Satel", model=d.get("model") or "Virtual IP device",
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

    @property
    def extra_state_attributes(self):
        d = self._dev()
        return {"room": d.get("room"), "device_id": self._devid,
                "input_violated": d.get("state") == 1, "output_type": d.get("output_type")}

    async def _set(self, on: bool):
        await self.hass.async_add_executor_job(self._hub.set_on_off, self._devid, on)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)


class BeWaveSettingSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, hub, entry: ConfigEntry, key: str, name: str):
        super().__init__(coordinator)
        self._hub = hub
        self._key = key
        self._attr_name = name
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer="Satel",
            model="BE WAVE Smart HUB",
            name=f"BE WAVE {serial}",
        )

    @property
    def is_on(self) -> bool | None:
        # None -> Home Assistant shows the entity as unknown until first read
        return self._hub.flags.get(self._key)

    async def async_turn_on(self, **kwargs):
        await self.hass.async_add_executor_job(self._hub.set_toggle, self._key, True)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.hass.async_add_executor_job(self._hub.set_toggle, self._key, False)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
