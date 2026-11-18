"""House-wide heating mode: one select that switches every thermostat head of the hub."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bewave_client as proto
from .climate import PRESETS, apply_preset, heater_of, preset_of
from .const import DOMAIN

MIXED = "mixed"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BeWaveHeatingMode(data["coordinator"], data["hub"], entry)])


class BeWaveHeatingMode(CoordinatorEntity, SelectEntity):
    """Selecting a preset applies it to all ART-210 heads (also ones added later).

    State = the common preset of all heads, ``mixed`` when they differ.
    Unavailable while the hub has no thermostat heads.
    """
    _attr_has_entity_name = True
    _attr_translation_key = "heating_mode"

    def __init__(self, coordinator, hub, entry: ConfigEntry):
        super().__init__(coordinator)
        self._hub = hub
        serial = entry.data.get("serial") or entry.entry_id
        self._attr_unique_id = f"bewave_{serial}_heating_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)}, manufacturer="Satel",
            model="BE WAVE Smart HUB", name=f"BE WAVE {serial}")

    def _heads(self) -> list[int]:
        return [d["id"] for d in self._hub.devices() if d.get("hwtype") == proto.ART_210]

    @property
    def available(self) -> bool:
        return super().available and bool(self._heads())

    @property
    def options(self) -> list[str]:
        return [*PRESETS, MIXED]

    @property
    def current_option(self) -> str | None:
        presets = {preset_of(heater_of(self._hub, i)) for i in self._heads()}
        presets.discard(None)
        if not presets:
            return None
        return presets.pop() if len(presets) == 1 else MIXED

    @property
    def extra_state_attributes(self):
        return {"heads": len(self._heads())}

    async def async_select_option(self, option: str) -> None:
        if option == MIXED:
            return                      # "mixed" is a state, not something to apply
        def _run():
            errors = []
            for devid in self._heads():
                if preset_of(heater_of(self._hub, devid)) == option:
                    continue
                try:
                    apply_preset(self._hub, devid, option)
                except Exception as err:  # noqa: BLE001 - keep going with the other heads
                    errors.append(f"{devid}: {err}")
            if errors:
                raise HomeAssistantError("Some heads did not switch: " + "; ".join(errors))
        try:
            await self.hass.async_add_executor_job(_run)
        finally:
            # refresh every entity (thermostats show the new preset right away)
            self.coordinator.async_update_listeners()
