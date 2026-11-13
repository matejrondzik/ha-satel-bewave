"""Repair flow: choose what is connected to the panel's wired zones (raised after setup)."""
from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from . import zones as zn
from .const import DOMAIN


class ZoneTypesFlow(RepairsFlow):
    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_zones()

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        hub = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {}).get("hub") if entry else None
        if entry is None or hub is None:
            return self.async_abort(reason="not_loaded")
        form, keys = zn.schema(hub, zn.current_classes(self.hass, entry, hub))
        if user_input is not None:
            # saving the options reloads the entry; the issue is re-evaluated (and deleted) on setup
            zn.clear_overrides(self.hass, entry, hub)
            self.hass.config_entries.async_update_entry(entry, options=zn.merge(dict(entry.options), user_input, keys))
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="zones", data_schema=form,
                                    description_placeholders={"zones": ", ".join(keys)})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    return ZoneTypesFlow((data or {})["entry_id"])
