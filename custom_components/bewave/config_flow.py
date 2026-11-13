"""Config flow for BE WAVE (Satel).

Steps
-----
* ``user``        – hub IP + login + password (prefilled IP when found via DHCP)
* ``dhcp``        – a Satel device (MAC 00:1B:9C) appeared on the network → confirm
* ``reauth``      – the hub rejected the stored password → ask for a new one
* ``reconfigure`` – the hub moved to another IP address
* options         – protection mode used for arm / disarm

Home Assistant signs in like the Be Wave app and registers itself as a new mobile
device of the given user. The device token is created once and reused on reauth /
reconfigure, so a password or IP change never consumes another device slot.
"""
from __future__ import annotations

import contextlib
import logging
import secrets
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import bewave_client as proto
from .const import (
    CONF_APP_REGISTERED,
    CONF_DEV_SERVICES,
    CONF_DEVICE_UUID,
    CONF_HOST,
    CONF_LOGIN,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_SERIAL,
    DEFAULT_MODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))
LOGIN = TextSelector(TextSelectorConfig(autocomplete="username"))
HOST = TextSelector()


async def _sign_in(hass: HomeAssistant, host: str, login: str, password: str,
                   device_uuid: str, serial: str | None = None,
                   entry: ConfigEntry | None = None) -> str | None:
    """Connect and sign in once. Returns the hub serial. Raises BeWave*Error / OSError.

    For an existing entry the running integration's session is paused meanwhile: the hub
    accepts only one session per device token.
    """
    hub = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("hub") if entry else None

    def _do():
        client = proto.BeWaveClient(login, password, device_uuid)
        if serial:
            client.serial = serial
        with hub.paused() if hub is not None else contextlib.nullcontext():
            conn = proto.LocalConnection(host, client)
            try:
                conn.connect_and_signin()
                return client.serial
            finally:
                conn.close()
    return await hass.async_add_executor_job(_do)


async def _try_sign_in(hass: HomeAssistant, errors: dict[str, str], **kw) -> str | None:
    """Sign in and translate failures into form errors. Returns serial or None on error."""
    try:
        return await _sign_in(hass, **kw) or ""
    except proto.BeWaveAuthError:
        errors["base"] = "invalid_auth"
    except proto.BeWaveDeviceLimitError:
        errors["base"] = "device_limit"
    except Exception as err:  # noqa: BLE001 - socket / timeout / protocol
        _LOGGER.warning("BE WAVE sign-in to %s failed: %s", kw.get("host"), err)
        errors["base"] = "cannot_connect"
    return None


class BeWaveConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None

    # ---- discovery ---------------------------------------------------------
    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """A Satel device (00:1B:9C…) got an address — offer to set it up."""
        self._host = discovery_info.ip
        self._async_abort_entries_match({CONF_HOST: self._host})
        mac = discovery_info.macaddress.replace(":", "").upper()
        await self.async_set_unique_id(f"bewave_mac_{mac}")
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": f"BE WAVE ({self._host})"}
        return await self.async_step_user()

    # ---- new hub -----------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            self._async_abort_entries_match({CONF_HOST: host})
            device_uuid = secrets.token_hex(16).upper()
            serial = await _try_sign_in(self.hass, errors, host=host,
                                        login=user_input[CONF_LOGIN],
                                        password=user_input[CONF_PASSWORD],
                                        device_uuid=device_uuid)
            if serial is not None:
                if serial:
                    await self.async_set_unique_id(f"bewave_{serial}", raise_on_progress=False)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=f"BE WAVE {serial}".strip() if serial else f"BE WAVE ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_LOGIN: user_input[CONF_LOGIN],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_UUID: device_uuid,
                        CONF_SERIAL: serial or None,
                        CONF_APP_REGISTERED: True,
                    },
                    options={CONF_MODE: DEFAULT_MODE},
                )
            user_input.pop(CONF_PASSWORD, None)

        defaults = user_input or {CONF_HOST: self._host or ""}
        schema = self.add_suggested_values_to_schema(vol.Schema({
            vol.Required(CONF_HOST): HOST,
            vol.Required(CONF_LOGIN): LOGIN,
            vol.Required(CONF_PASSWORD): PASSWORD,
        }), defaults)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ---- password changed --------------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            ok = await _try_sign_in(self.hass, errors, host=entry.data[CONF_HOST],
                                    login=user_input[CONF_LOGIN],
                                    password=user_input[CONF_PASSWORD],
                                    device_uuid=entry.data[CONF_DEVICE_UUID],
                                    serial=entry.data.get(CONF_SERIAL), entry=entry)
            if ok is not None:
                return self._update_and_abort(entry, "reauth_successful", {
                    CONF_LOGIN: user_input[CONF_LOGIN], CONF_PASSWORD: user_input[CONF_PASSWORD]})
        schema = self.add_suggested_values_to_schema(vol.Schema({
            vol.Required(CONF_LOGIN): LOGIN,
            vol.Required(CONF_PASSWORD): PASSWORD,
        }), {CONF_LOGIN: entry.data[CONF_LOGIN]})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors,
                                    description_placeholders={"host": entry.data[CONF_HOST]})

    # ---- IP address changed -----------------------------------------------
    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            serial = await _try_sign_in(self.hass, errors, host=host,
                                        login=entry.data[CONF_LOGIN],
                                        password=entry.data[CONF_PASSWORD],
                                        device_uuid=entry.data[CONF_DEVICE_UUID], entry=entry)
            if serial is not None:
                if serial and entry.unique_id and entry.unique_id.startswith("bewave_") \
                        and not entry.unique_id.startswith("bewave_mac_"):
                    await self.async_set_unique_id(f"bewave_{serial}")
                    self._abort_if_unique_id_mismatch(reason="wrong_hub")
                return self._update_and_abort(entry, "reconfigure_successful", {CONF_HOST: host})
        schema = self.add_suggested_values_to_schema(
            vol.Schema({vol.Required(CONF_HOST): HOST}), {CONF_HOST: entry.data[CONF_HOST]})
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    def _update_and_abort(self, entry: ConfigEntry, reason: str, updates: dict[str, Any]) -> ConfigFlowResult:
        """Store new data; the entry's update listener reloads the integration."""
        if self.hass.config_entries.async_update_entry(entry, data={**entry.data, **updates}) is False:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)   # unchanged data
        return self.async_abort(reason=reason)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BeWaveOptionsFlow()


class BeWaveOptionsFlow(OptionsFlow):
    """Which protection mode the alarm panel entity arms / disarms; developer services on / off."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_MODE: user_input[CONF_MODE].strip() or DEFAULT_MODE,
                                                 CONF_DEV_SERVICES: bool(user_input.get(CONF_DEV_SERVICES))})
        entry = self.config_entry
        current = entry.options.get(CONF_MODE, entry.data.get(CONF_MODE, DEFAULT_MODE))
        schema = vol.Schema({
            vol.Required(CONF_MODE, default=current): SelectSelector(SelectSelectorConfig(
                options=[DEFAULT_MODE], custom_value=True, mode=SelectSelectorMode.DROPDOWN,
                translation_key="mode")),
            vol.Optional(CONF_DEV_SERVICES, default=entry.options.get(CONF_DEV_SERVICES, False)): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
