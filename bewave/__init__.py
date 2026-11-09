"""BE WAVE (Satel) integration for Home Assistant."""
from __future__ import annotations
import logging
import secrets
import threading
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (DOMAIN, CONF_LOGIN, CONF_PASSWORD, CONF_HOST, CONF_SERIAL,
                    CONF_DEVICE_UUID, CONF_MODE, DEFAULT_MODE, SCAN_INTERVAL_SECONDS,
                    CONF_APP_REGISTERED)
from . import bewave_client as proto

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["alarm_control_panel", "switch", "binary_sensor", "sensor", "climate"]


class BeWaveHub:
    """Owns a persistent local connection; thread-safe for executor jobs."""

    def __init__(self, host, login, password, device_uuid, serial=None, mode="defau"):
        self.host = host
        self.mode = mode
        self._lock = threading.Lock()
        self.client = proto.BeWaveClient(login, password, device_uuid)
        if serial:
            self.client.serial = serial
        self.conn: proto.LocalConnection | None = None
        self._registered = False
        self._empty = 0                        # consecutive polls with no data
        self.state: str | None = None          # armed_away | disarmed | None
        self.flags = {"led": None, "grade2": None, "satel": None}
        self.info = {"power": None, "stor_free": None, "stor_total": None,
                     "network": None, "firmware": None}
        self.dev_names: dict[int, dict] = {}   # config: name/room/model/sysnum/bypass
        self.dev_state: dict[int, dict] = {}   # live telemetry
        self.on_update = None                  # set by setup: push new data to HA
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- keepalive (HYBRID closes idle sessions after ~2.7 s) ----
    def start_keepalive(self):
        self._thread = threading.Thread(target=self._keepalive_loop,
                                        name="bewave-keepalive", daemon=True)
        self._thread.start()

    def stop_keepalive(self):
        self._stop.set()

    def _snapshot(self):
        return (self.state, repr(sorted(self.dev_state.items())), repr(self.flags))

    def _keepalive_loop(self):
        while not self._stop.is_set():
            changed = False
            # never wait for a long poll/command; they keep the session alive too
            if self._lock.acquire(timeout=0.2):
                try:
                    if self.conn is not None:
                        before = self._snapshot()
                        try:
                            self.conn.refresh()
                            msgs = self.conn.read_messages(0.3)
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.debug("BE WAVE keepalive: session lost: %s", err)
                            try:
                                self.conn.close()
                            finally:
                                self.conn = None
                        else:
                            if msgs:
                                self._empty = 0
                                self._update_from(msgs)
                                changed = self._snapshot() != before
                finally:
                    self._lock.release()
            if changed and self.on_update:
                self.on_update()
            self._stop.wait(proto.KEEPALIVE_S - 0.3)

    def _ensure(self):
        if self.conn is not None:
            return
        self.conn = proto.LocalConnection(self.host, self.client)
        self.conn.connect_and_signin()         # subscribes + registers
        if not self._registered:
            # firmware 1.04+: a brand-new self-generated device id gets FULL access
            # (state + control) only AFTER it registers, the hub processes the
            # registration (and the user approves the "new device" on their phone if
            # prompted), and we then reconnect. The wait is essential — reconnecting
            # immediately leaves the device read-only (it can read state but the hub
            # ignores its arm/disarm commands). Proven with bewave_ownuuid.py.
            self._registered = True
            try:
                self.conn.set_name("Home Assistant")
                self.conn.read_messages(1)
            except Exception:  # noqa: BLE001 - naming is cosmetic
                pass
            self.conn.close()
            self.conn = None
            time.sleep(8)
            self.conn = proto.LocalConnection(self.host, self.client)
            self.conn.connect_and_signin()
        _LOGGER.info("BE WAVE: signed in to %s (serial=%s)", self.host, self.client.serial)

    def _reconnect(self):
        """Drop a dead/stale session and sign in again (caller holds the lock)."""
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        self._empty = 0
        self._ensure()

    def poll(self):
        with self._lock:
            try:
                self._ensure()
                self.conn.refresh()            # light re-subscribe (f88 + f26)
                msgs = self.conn.read_messages(1.5)
                if msgs:
                    self._empty = 0
                    self._update_from(msgs)
                else:
                    # A healthy session answers a refresh with telemetry; several
                    # silent polls in a row mean the hub dropped us -> reconnect.
                    self._empty += 1
                    if self._empty >= 3:
                        _LOGGER.info("BE WAVE: stale session, reconnecting")
                        self._reconnect()
                return {"state": self.state}
            except Exception as err:  # noqa: BLE001
                # One immediate reconnect before reporting a failure, so a session the
                # hub dropped (e.g. when the phone app connects) does not flap entities.
                try:
                    self._reconnect()
                    msgs = self.conn.read_messages(1.5)
                    self._update_from(msgs)
                    return {"state": self.state}
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if self.conn: self.conn.close()
                finally:
                    self.conn = None
                self._empty = 0
                raise UpdateFailed(f"BE WAVE poll failed: {err}") from err

    def _update_from(self, msgs):
        for _fd, pt in msgs:
            if not pt:
                continue
            st = proto.armed_state(pt)
            if st is not None:
                self.state = "armed_away" if st else "disarmed"
            fl = proto.system_status(pt)
            if fl is not None:
                self.flags.update(fl)
            hi = proto.hub_info(pt)
            if hi is not None:
                self.info.update({"power": hi["power"], "stor_free": hi["stor_free"],
                                  "stor_total": hi["stor_total"]})
                if hi.get("firmware"):
                    self.info["firmware"] = hi["firmware"]
            net = proto.network_active(pt)
            if net is not None:
                self.info["network"] = net
            cfg = proto.parse_config_devices(pt)
            for did, d in cfg.items():
                self.dev_names[did] = {k: d[k] for k in ("name", "room", "type", "model", "sysnum", "bypass", "hwtype")}
                ds = self.dev_state.setdefault(did, {})
                for k in ("signal", "battery", "state", "temp", "type", "volt", "on_off", "heater"):
                    if d.get(k) is not None:
                        ds[k] = d[k]
            for did, d in proto.parse_telemetry(pt).items():
                self.dev_state.setdefault(did, {}).update({k: v for k, v in d.items() if v is not None})
        # while armed, a currently-violated contact/motion zone means the alarm has
        # been tripped -> show the panel as "triggered" (sounding)
        if self.state == "armed_away":
            for _did, _st in self.dev_state.items():
                _typ = self.dev_names.get(_did, {}).get("type") or _st.get("type")
                if _st.get("state") == 1 and proto.DEV_TYPES.get(_typ) in ("contact", "motion"):
                    self.state = "triggered"
                    break

    def devices(self):
        out = []
        for did in set(self.dev_names) | set(self.dev_state):
            nm = self.dev_names.get(did, {}); st = self.dev_state.get(did, {})
            typ = nm.get("type") or st.get("type")
            out.append({"id": did, "name": nm.get("name") or f"#{did}",
                        "room": nm.get("room"), "model": nm.get("model"),
                        "sysnum": nm.get("sysnum"), "bypass": nm.get("bypass"),
                        "cat": proto.DEV_TYPES.get(typ, "device"),
                        "state": st.get("state"), "temp": st.get("temp"),
                        "battery": st.get("battery"), "signal": st.get("signal"),
                        "volt": st.get("volt"), "hwtype": nm.get("hwtype"),
                        "on_off": st.get("on_off"), "heater": st.get("heater")})
        return out

    def _command(self, send, verify=None):
        """Run a command on a live session, verifying + retrying on a fresh
        connection if the socket was stale (commands are lost on a dead socket).
        Caller holds the lock."""
        last = None
        for attempt in (1, 2):
            try:
                self._ensure()
                send()
                # A live session answers with telemetry; no reply == dropped
                # socket (half-open), so require real data before trusting it.
                msgs = self.conn.read_messages(2)
                self._update_from(msgs)
                if msgs and (verify is None or verify()):
                    self._empty = 0
                    return True
            except Exception as err:
                last = err
            # stale, silent or unverified -> reconnect and try once more
            self._reconnect()
        if last:
            _LOGGER.warning("BE WAVE: command failed: %s", last)
        return False

    def arm(self):
        with self._lock:
            # do not claim "armed" until the hub confirms via read-back
            self._command(lambda: self.conn.arm(self.mode),
                          verify=lambda: self.state == "armed_away")
    def disarm(self):
        with self._lock:
            self._command(lambda: self.conn.disarm(self.mode),
                          verify=lambda: self.state == "disarmed")
    def set_on_off(self, devid, desired):
        """Switch a device output (e.g. a virtual IP device) on/off."""
        with self._lock:
            self._command(lambda: self.conn.set_on_off(devid, desired),
                          verify=lambda: self.dev_state.get(devid, {}).get("on_off") == desired)

    def set_patch(self, devid, command, verify=None, **values):
        """Send one DeviceStateUpdate patch (e.g. heater target / mode) and verify via state."""
        with self._lock:
            return self._command(lambda: self.conn.send_patch(devid, command, **values),
                                 verify=verify)

    def set_toggle(self, name, desired):
        if name not in proto.SETTING_IDS:
            return
        with self._lock:
            def _send():
                cur = self.flags.get(name)
                if cur is None or cur != desired:
                    self.conn.toggle_setting(proto.SETTING_IDS[name])
                self.flags[name] = desired
            self._command(_send, verify=lambda: self.flags.get(name) == desired)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.data.get(CONF_APP_REGISTERED):
        # Older entries registered their device token without credentials, which the
        # hub treats as read-only for device control. Register a fresh token the way
        # the Be Wave app does (sign-in with credentials) on first start.
        hass.config_entries.async_update_entry(entry, data={
            **entry.data,
            CONF_DEVICE_UUID: secrets.token_hex(16).upper(),
            CONF_APP_REGISTERED: True,
        })
    hub = BeWaveHub(
        host=entry.data[CONF_HOST],
        login=entry.data[CONF_LOGIN],
        password=entry.data[CONF_PASSWORD],
        device_uuid=entry.data[CONF_DEVICE_UUID],
        serial=entry.data.get(CONF_SERIAL),
        mode=entry.options.get(CONF_MODE, entry.data.get(CONF_MODE, DEFAULT_MODE)),
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async def _update():
        return await hass.async_add_executor_job(hub.poll)

    coordinator = DataUpdateCoordinator(
        hass, _LOGGER, name=DOMAIN, update_method=_update,
        update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
    )
    await coordinator.async_config_entry_first_refresh()

    def _push():
        hass.loop.call_soon_threadsafe(
            coordinator.async_set_updated_data, {"state": hub.state})
    hub.on_update = _push
    hub.start_keepalive()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"hub": hub, "coordinator": coordinator}
    # Register the hub device first: sensor devices point to it via via_device,
    # which fails if a sensor platform happens to load before the alarm panel.
    serial = entry.data.get(CONF_SERIAL) or entry.entry_id
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, serial)},
        manufacturer="Satel",
        model="BE WAVE",
        name=f"BE WAVE {serial}",
        sw_version=hub.info.get("firmware"),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        hub: BeWaveHub = data["hub"]
        hub.stop_keepalive()
        if hub.conn:
            await hass.async_add_executor_job(hub.conn.close)
    return ok
