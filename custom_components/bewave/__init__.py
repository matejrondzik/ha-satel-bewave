"""BE WAVE (Satel) integration for Home Assistant."""
from __future__ import annotations
import asyncio
import contextlib
import logging
import secrets
import threading
import time
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr, discovery_flow
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (DOMAIN, CONF_LOGIN, CONF_PASSWORD, CONF_HOST, CONF_SERIAL,
                    CONF_DEVICE_UUID, CONF_MODE, DEFAULT_MODE, SCAN_INTERVAL_SECONDS,
                    CONF_APP_REGISTERED, CONF_DEV_SERVICES)
from . import bewave_client as proto

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["alarm_control_panel", "switch", "binary_sensor", "sensor", "climate", "select", "light", "number"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
DISCOVERY_INTERVAL = timedelta(minutes=30)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Look for hubs on the LAN (UDP 4111) after start and every 30 min: new hubs show up under
    *Discovered*, a configured hub that got a new IP address is updated (the flow aborts with the new host)."""

    async def _scan(_now=None) -> None:
        targets = ["255.255.255.255"]
        try:
            from homeassistant.components import network
            targets += [str(a) for a in await network.async_get_ipv4_broadcast_addresses(hass)]
        except Exception:  # noqa: BLE001
            pass
        try:
            found = await hass.async_add_executor_job(proto.discover, tuple(dict.fromkeys(targets)), 2.0)
        except OSError as err:
            _LOGGER.debug("BE WAVE discovery failed: %s", err)
            return
        entries = {e.unique_id: e for e in hass.config_entries.async_entries(DOMAIN)}
        for serial, info in found.items():
            e = entries.get(f"bewave_{serial}")
            if e is not None and e.data.get(CONF_HOST) == info["host"]:
                continue                     # known hub at the same address - nothing to do
            discovery_flow.async_create_flow(hass, DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY}, data=info)

    async def _started(_event=None) -> None:
        await _scan()
        async_track_time_interval(hass, _scan, DISCOVERY_INTERVAL, cancel_on_shutdown=True)

    if hass.is_running:
        hass.async_create_task(_started())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
    return True


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
        self.schedules = None            # heating schedules from the hub (None = not received yet)
        self.scenes = None               # scenes + routines from the hub (None = not received yet)
        self.scene_reply = {}            # last StructureUpsert/UpdateState/Delete scene replies (71/73/75)
        self.info = {"power": None, "stor_free": None, "stor_total": None,
                     "network": None, "firmware": None, "errors": None, "errors_memory": None}
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

    @contextlib.contextmanager
    def paused(self):
        """Release the hub session for a moment (blocking; run in executor).

        The hub allows only one session per registered device token, so the config
        flow must borrow it to verify a new IP / password. Poll reconnects afterwards.
        """
        with self._lock:
            try:
                if self.conn:
                    self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None
            self._empty = 0
            yield

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
                auth_err = None
                try:
                    self._reconnect()
                    msgs = self.conn.read_messages(1.5)
                    self._update_from(msgs)
                    return {"state": self.state}
                except proto.BeWaveAuthError as e:
                    auth_err = e
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if self.conn: self.conn.close()
                finally:
                    self.conn = None
                self._empty = 0
                if auth_err is not None or isinstance(err, proto.BeWaveAuthError):
                    # password changed in the Be Wave app -> HA shows "Re-authenticate"
                    raise ConfigEntryAuthFailed("BE WAVE hub rejected the login") from err
                raise UpdateFailed(f"BE WAVE poll failed: {err}") from err

    def _update_from(self, msgs):
        for _fd, pt in msgs:
            if not pt:
                continue
            top = {}
            try:  # keep the last raw config / schedule list for debugging (bewave.debug_send dump)
                top = proto.pb_read(pt)
                for k in (45, 47, 51):
                    if k in top:
                        self.last_raw = getattr(self, "last_raw", {})
                        self.last_raw[k] = top[k][-1].hex()
                        if k == 47:  # schedule list can come in several messages - keep them all
                            hist = self.last_raw.setdefault("47_all", [])
                            hist.extend(f"{time.strftime('%H:%M:%S')} {v.hex()}" for v in top[k])
                            del hist[:-10]
            except Exception:  # noqa: BLE001
                pass
            st = proto.armed_state(pt)
            if st is not None:
                self.state = "armed_away" if st else "disarmed"
            fl = proto.system_status(pt)
            if fl is not None:
                self.flags.update(fl)
            hi = proto.hub_info(pt)
            if hi is not None:
                self.info.update({"power": hi["power"], "stor_free": hi["stor_free"],
                                  "stor_total": hi["stor_total"], "errors": hi["errors"],
                                  "errors_memory": hi["errors_memory"]})
                if hi.get("firmware"):
                    self.info["firmware"] = hi["firmware"]
            net = proto.network_active(pt)
            if net is not None:
                self.info["network"] = net
            sch = proto.parse_schedules(pt)
            if sch is not None:
                self.schedules = sch
            scn = proto.parse_scenes(pt)
            if scn is not None:
                self.scenes = scn
            for k in (71, 73, 75):
                if k in top:
                    self.scene_reply[k] = (_fd or {}).get(10, 0) if isinstance(_fd, dict) else 0
            cfg = proto.parse_config_devices(pt)
            for did, d in cfg.items():
                self.dev_names[did] = {k: d[k] for k in ("name", "room", "type", "model", "sysnum", "bypass", "hwtype", "output_type")}
                ds = self.dev_state.setdefault(did, {})
                for k in ("signal", "battery", "state", "temp", "type", "volt", "on_off", "heater", "errors",
                          "keypad_block", "options", "raw"):
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
                        "volt": st.get("volt"), "hwtype": nm.get("hwtype"), "output_type": nm.get("output_type"),
                        "on_off": st.get("on_off"), "heater": st.get("heater"),
                        "errors": st.get("errors"), "keypad_block": st.get("keypad_block"),
                        "options": st.get("options"), "raw": st.get("raw")})
        return out

    def _command(self, send, verify=None):
        """Run a command on a live session, verifying + retrying on a fresh
        connection if the socket was stale (commands are lost on a dead socket).
        Caller holds the lock."""
        last = None
        # the hub drops a command that arrives right after the previous one -> keep a minimal gap
        gap = 1.5 - (time.monotonic() - getattr(self, "_last_cmd", 0))
        if gap > 0:
            time.sleep(gap)
        for attempt in (1, 2):
            try:
                self._ensure()
                send()
                self._last_cmd = time.monotonic()
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

    def write_schedules(self, schedules):
        """Replace the hub's schedule list (StructureUpdateSchedule). One request = one message (the hub drops the
        connection on multi-patch requests, so nothing else is bundled)."""
        with self._lock:
            ok = self._command(lambda: self.conn.send_command(proto.schedules_plaintext(schedules)))
            if ok:
                self.schedules = [dict(x) for x in schedules]
            return ok

    # ---- scenes / routines (stored and run by the hub itself, also without Home Assistant) ----------------
    def _scene_cmd(self, plaintext, reply_field):
        """One scene request; succeeds when the hub answers with its reply message."""
        with self._lock:
            self.scene_reply.pop(reply_field, None)
            ok = self._command(lambda: self.conn.send_command(plaintext),
                                 verify=lambda: reply_field in self.scene_reply)
            if ok and self.scene_reply.get(reply_field):
                _LOGGER.warning("BE WAVE: scene request %s refused, error %s", reply_field - 1, self.scene_reply[reply_field])
                return False
            return ok

    def upsert_scene(self, scene):
        bad = {a.get("type") for a in (scene.get("actions") or []) + (scene.get("else_actions") or [])} & proto.SCENE_FORBIDDEN_ACTIONS
        if bad:
            raise ValueError(f"actions not allowed from Home Assistant: {', '.join(sorted(bad))}")
        is_update = any(x["id"] == scene["id"] for x in (self.scenes or []))
        ok = self._scene_cmd(proto.scene_upsert_plaintext(scene, is_update), 71)
        if ok:
            lst = [x for x in (self.scenes or []) if x["id"] != scene["id"]]
            self.scenes = lst + [dict(scene)]
        return ok

    def delete_scene(self, scene_id):
        ok = self._scene_cmd(proto.scene_delete_plaintext(scene_id), 75)
        if ok and self.scenes is not None:
            self.scenes = [x for x in self.scenes if x["id"] != scene_id]
        return ok

    def set_scene_state(self, scene_id, state):
        return self._scene_cmd(proto.scene_state_plaintext(scene_id, state), 73)

    def assign_schedule(self, devid, schedule_id, activate):
        """Give a head a schedule ("" = none) and optionally switch it to schedule mode. Patches go one by one with a
        pause: assigning a schedule puts the head back to manual mode."""
        h = lambda: next((d.get("heater") or {} for d in self.devices() if d["id"] == devid), {})
        if h().get("schedule") != schedule_id:
            self.set_patch(devid, proto.SET_SCHEDULE, string=schedule_id, verify=lambda: h().get("schedule") == schedule_id)
            time.sleep(3)
        mode = 1 if (activate and schedule_id) else 0
        if h().get("mode") != mode or (mode == 0 and not schedule_id):
            self.set_patch(devid, proto.SET_HEATER_WORKING_MODE, number=mode, verify=lambda: h().get("mode") == mode)

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

    # Device models arrive with the hub configuration, which can come after the entities
    # registered their devices -> fill in missing models once they are known.
    serial_id = entry.data.get(CONF_SERIAL) or entry.entry_id
    done_models: set[int] = set()

    def _sync_models():
        reg = dr.async_get(hass)
        for d in hub.devices():
            if d["id"] in done_models or not d.get("model"):
                continue
            dev = reg.async_get_device(identifiers={(DOMAIN, f"{serial_id}_{d['id']}")})
            if dev is None:
                continue
            if dev.model != d["model"]:
                reg.async_update_device(dev.id, model=d["model"])
            done_models.add(d["id"])

    entry.async_on_unload(coordinator.async_add_listener(_sync_models))
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
    _register_scene_services(hass)
    if entry.options.get(CONF_DEV_SERVICES):
        _register_debug_service(hass)
    return True


def _register_scene_services(hass: HomeAssistant) -> None:
    """bewave.save_scene / delete_scene / run_scene - scenes and routines live in the hub and run there
    (also when Home Assistant or the network is down). Arming, disarming and alarm actions are refused."""
    if hass.services.has_service(DOMAIN, "save_scene"):
        return

    def _hub() -> BeWaveHub:
        return next(iter(hass.data[DOMAIN].values()))["hub"]

    def _norm(d):
        sc = dict(d)
        sc.setdefault("id", secrets.token_hex(3)[:5])
        sc.setdefault("type", "routine")
        sc.setdefault("icon", "lamp-2")
        sc.setdefault("enabled", True)
        for c in sc.get("conditions") or []:
            c.setdefault("id", secrets.token_hex(3)[:5])
        return sc

    async def _save(call: ServiceCall):
        hub = _hub()
        sc = _norm(call.data["scene"])
        try:
            ok = await hass.async_add_executor_job(hub.upsert_scene, sc)
        except ValueError as err:
            return {"ok": False, "error": str(err)}
        return {"ok": ok, "scene": sc}

    async def _delete(call: ServiceCall):
        ok = await hass.async_add_executor_job(_hub().delete_scene, call.data["scene_id"])
        return {"ok": ok}

    async def _run(call: ServiceCall):
        ok = await hass.async_add_executor_job(_hub().set_scene_state, call.data["scene_id"], call.data.get("state", "start"))
        return {"ok": ok}

    hass.services.async_register(DOMAIN, "save_scene", _save, schema=vol.Schema({vol.Required("scene"): dict}),
                                 supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "delete_scene", _delete, schema=vol.Schema({vol.Required("scene_id"): str}),
                                 supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "run_scene", _run,
                                 schema=vol.Schema({vol.Required("scene_id"): str,
                                                    vol.Optional("state", default="start"): vol.In(list(proto.SCENE_STATES))}),
                                 supports_response=SupportsResponse.OPTIONAL)


def _register_debug_service(hass: HomeAssistant) -> None:
    """Developer services (enabled in the integration options), for mapping new devices / fields.
    debug_patch: send one raw DeviceStateUpdate patch and return the device's raw state before/after.
    debug_send: send a raw plaintext message (hex) and return the hub's reply fields.
    WARNING: they bypass every safety check - a wrong message can e.g. restart or reset the hub."""
    if hass.services.has_service(DOMAIN, "debug_patch"):
        return

    async def _handle(call: ServiceCall):
        hub: BeWaveHub = next(iter(hass.data[DOMAIN].values()))["hub"]
        devid, cmd = int(call.data["device"]), int(call.data["command"])
        kw = {k: call.data[k] for k in ("boolean", "number", "flt", "string") if k in call.data}
        st = lambda: dict(hub.dev_state.get(devid, {}))
        before = st()
        ok = await hass.async_add_executor_job(lambda: hub.set_patch(devid, cmd, **kw))
        await asyncio.sleep(float(call.data.get("wait", 3)))
        after = st()
        return {"ok": ok, "options": after.get("options"),
                "before": before.get("raw"), "after": after.get("raw"),
                "on_off": [before.get("on_off"), after.get("on_off")]}

    hass.services.async_register(DOMAIN, "debug_patch", _handle,
                                 supports_response=SupportsResponse.OPTIONAL)

    async def _send(call: ServiceCall):
        """RE helper: send raw plaintext (hex) on the integration's session, return the reply fields (hex)."""
        hub: BeWaveHub = next(iter(hass.data[DOMAIN].values()))["hub"]
        pt = bytes.fromhex(call.data["hex"])
        want = {int(x) for x in call.data.get("fields", [])}
        if not pt:  # dump the last raw config/schedules to a file instead of sending anything
            raw = dict(getattr(hub, "last_raw", {}))
            def _w():
                import json as _j
                with open(hass.config.path("bewave_last_raw.json"), "w") as f:
                    _j.dump(raw, f)
            await hass.async_add_executor_job(_w)
            return {"saved": {k: (len(v) // 2 if isinstance(v, str) else len(v)) for k, v in raw.items()}}

        def _run():
            with hub._lock:
                hub._ensure()
                hub.conn.send_command(pt)
                msgs = hub.conn.read_messages(float(call.data.get("wait", 3)))
                hub._update_from(msgs)
                return msgs
        msgs = await hass.async_add_executor_job(_run)
        out = []
        for fd, body in msgs:
            if not body:
                continue
            d = proto.pb_read(body)
            for k, vs in d.items():
                if not want or k in want:
                    out += [{"field": k, "err": fd.get(10), "hex": v.hex() if isinstance(v, bytes) else v} for v in vs]
        return {"messages": out}

    hass.services.async_register(DOMAIN, "debug_send", _send, supports_response=SupportsResponse.OPTIONAL)


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
