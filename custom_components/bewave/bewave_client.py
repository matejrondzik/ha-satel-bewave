"""
BE WAVE (Satel) protocol client — local TCP/4200.
Reverse-engineered from pl.satel.bewave. Crypto verified on a demo unit (fw 1.04).

Self-contained & runnable standalone for testing:
    python3 bewave_client.py status --host 192.168.1.50 --login USER --password 'PASS'
    python3 bewave_client.py arm    --host 192.168.1.50 --login USER --password 'PASS'
    python3 bewave_client.py disarm --host 192.168.1.50 --login USER --password 'PASS'

Key chain (AES-256-GCM, IV = randomNumber||timestamp||counter, each BE32):
  sign-in  app->hub : key MD5(pw)*2            aad login
  sign-in  hub->app : key deviceUuid(ASCII)    aad MD5(login padded 64)
  ongoing  app->hub : key deviceUuid(ASCII)    aad MD5(sessionId)
  ongoing  hub->app : key sessionSecret(f4)    aad MD5(sessionId)

Authorisation (firmware 1.04+): any self-chosen device receives live state after
it registers (f8 push-token message) and then reconnects. See LocalConnection.

State / data sources:
  f89  device telemetry  -> armed state (field 20), per-device open/motion (f9),
                            battery % (f4) / voltage (f110), signal (f3), temp.
  f27  hub status        -> LED/GRADE2/SATEL toggles, power, storage, firmware.
  f45  config            -> device names, rooms, model, system number.
  f31  connection methods-> active network (LAN/SIM).
"""
import socket, struct, hashlib, time, secrets, sys

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    raise SystemExit("pip install cryptography")

DATA_PORT = 4200
# Subscribe after sign-in so the hub starts sending state/config.
STATE_SUBS = [88, 44, 26, 52, 46, 56, 58, 90, 24, 30]
# Light re-subscribe used every poll to refresh device + settings (small + reliable).
REFRESH_SUBS = [88, 26]
ALARM_ARMED_FIELD = 20
# Site-setting toggles. Command is a FLIP: f34{f1{f1=id, f2{f3:{}}}}.
SETTING_IDS = {"led": 5, "grade2": 12, "satel": 11}
# device category by telemetry field f10
DEV_TYPES = {0x10002: "keyfob", 0x10003: "motion", 0x10007: "contact",
             # HYBRID 128 (fw 1.05) wired BUS detectors reported as these types
             0x1004F: "motion", 0x10051: "motion"}
# HubDeviceType values (config f2) and HubDeviceState_Settings_Command values
VIRTUAL_IP_DEVICE = 69
AFD_200 = 18                     # flood detector (state f9: 1 = water)
ART_210 = 35                     # thermostatic radiator head
SET_ON_OFF = 1
SET_ALARM_OPTICAL_DURATION = 21  # number, seconds (app allows 0..180)
ALARM_OPTICAL_DURATION_FIELD = 23
LUNA_TYPES = {58, 59, 61, 62, 79, 80}   # SLIM-*-LUNA-* detectors with a light ring
SET_HEATER_WORKING_MODE = 60     # number: 0 manual, 1 schedule, 2 eco
SET_HEATER_FORCED_VALUE = 61     # Float: active target temperature
SET_HEATER_BOOST = 107           # boolean
# heater preset temperatures / settings (Float f13 unless noted) - as the Be Wave app sends them
SET_HEATER_TEMP_DAY = 62
SET_HEATER_TEMP_NIGHT = 63
SET_HEATER_TEMP_ECO = 64
SET_HEATER_BOOST_TIME = 65       # number, milliseconds
SET_HEATER_OFFSET = 66
SET_HEATER_TEMP_MANUAL = 104     # stored in state field 95
HEATER_MODES = {0: "manual", 1: "schedule", 2: "eco"}
# HYBRID drops a session after ~2.7 s without traffic; refresh well below that.
KEEPALIVE_S = 1.0
# device model by config field f2 (verified from the app)
DEV_MODELS = {  # HubDeviceType (device config field 2) -> model name
    0: "External camera", 1: "Hub main board", 10: "AOD-210", 11: "APD-200", 12: "APCAM-200", 13: "APD-200 Pet",
    14: "APMD-250", 15: "AOCD-260", 16: "ACD-220", 17: "AXD-200", 18: "AFD-200", 19: "AGD-200",
    20: "ASD-200", 21: "ASD-210", 22: "ASD-250", 23: "ACMD-200", 24: "APB-210", 25: "ADD-200",
    26: "APT-210", 27: "ASW-200 E", 28: "ASW-210", 29: "ASP-200-R", 30: "ASP-215-R", 31: "ARSC-200",
    32: "ADC-200", 33: "ARC-200", 34: "AVC-200", 35: "ART-210", 36: "ATPH-200", 37: "ACX-210",
    38: "ACX-220", 39: "ATX-200", 40: "ATX-210", 41: "AKP-200", 42: "AXD-200 Lite", 43: "INT-E",
    44: "INT-O", 45: "INT-PP", 46: "INT-IORS", 47: "INT-ORS", 48: "INT-E-PS", 49: "INT-O-PS",
    50: "INT-PP-PS", 51: "INT-IORS-PS", 52: "INT-ORS-PS", 53: "TPH2", 54: "TSH2", 55: "TSG2",
    56: "SO-MF5", 57: "SLIM-PIR-PRO-BUS", 58: "SLIM-PIR-LUNA-PET-BUS", 59: "SLIM-PIR-LUNA-BUS", 60: "SLIM-DUAL-PRO-BUS", 61: "SLIM-DUAL-LUNA-PET-BUS",
    62: "SLIM-DUAL-LUNA-BUS", 63: "AUT-200", 64: "APC-200", 65: "APMC-250", 66: "AOCAM-210", 67: "ARU-200",
    68: "ASW-220", 69: "Virtual IP device", 70: "APB-200", 71: "TSH2R", 72: "TSG2R", 73: "TSH210",
    74: "TSH210R", 75: "SO-MF2", 76: "SO-MF3", 77: "SLIM-SR-PIR-PRO-BUS", 78: "SLIM-SR-DUAL-PRO-BUS", 79: "SLIM-SR-PIR-LUNA-BUS",
    80: "SLIM-SR-DUAL-LUNA-BUS", 81: "INT-IT2MF", 82: "AXU-250", 83: "CR-MF3", 84: "CR-MF5",
}
KEYPAD_TYPES = {41, 53, 54, 55, 71, 72, 73, 74}   # AKP-200, TPH2/TSH2/TSG2(R), TSH210(R)

# ---------- helpers ----------
def md5(x): return hashlib.md5(x.encode() if isinstance(x, str) else x).digest()
def pad(b, n):
    b = b.encode() if isinstance(b, str) else b
    return b + bytes(n - len(b))
def _wv(n):
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: break
    return bytes(out)
def _rv(b, i):
    s = r = 0
    while True:
        if i >= len(b): raise IndexError
        x = b[i]; i += 1; r |= (x & 0x7f) << s; s += 7
        if not x & 0x80: break
    return r, i
def pb_bytes(field, data): return _wv((field << 3) | 2) + _wv(len(data)) + data
def pb_varint(field, val): return _wv((field << 3) | 0) + _wv(val)
def pb_fixed32(field, val): return _wv((field << 3) | 5) + struct.pack('<I', val)
def pb_read(b):
    """shallow read -> {field:[values]}"""
    o = {}; i = 0
    while i < len(b):
        try:
            tag, i = _rv(b, i); fn, wt = tag >> 3, tag & 7
            if wt == 0: v, i = _rv(b, i)
            elif wt == 5:
                if i + 4 > len(b): break
                v = struct.unpack_from('<I', b, i)[0]; i += 4
            elif wt == 1:
                if i + 8 > len(b): break
                v = struct.unpack_from('<Q', b, i)[0]; i += 8
            elif wt == 2:
                ln, i = _rv(b, i)
                if i + ln > len(b): break
                v = b[i:i+ln]; i += ln
            else: break
        except Exception:
            break
        o.setdefault(fn, []).append(v)
    return o
def _looks_str(b):
    return isinstance(b, (bytes, bytearray)) and len(b) >= 2 and all(32 <= c < 127 for c in b)
def _f32(v):
    try: return round(struct.unpack('<f', struct.pack('<I', v))[0], 2)
    except Exception: return None
def _s(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode('utf-8')
        except UnicodeDecodeError:
            return v.decode('latin1')
    return v or ""
def find_varint(buf, target, depth=0):
    res = []; i = 0
    while i < len(buf):
        try:
            tag, i = _rv(buf, i); fn, wt = tag >> 3, tag & 7
            if wt == 0:
                v, i = _rv(buf, i)
                if fn == target: res.append(v)
            elif wt == 5: i += 4
            elif wt == 1: i += 8
            elif wt == 2:
                ln, i = _rv(buf, i); sub = buf[i:i+ln]; i += ln
                if depth < 12 and not _looks_str(sub):
                    res += find_varint(sub, target, depth + 1)
            else: break
        except Exception:
            break
    return res

# ---------- decoders ----------
def armed_state(plaintext):
    """True=armed, False=disarmed, None=not a device-state message (field 20)."""
    if 89 not in pb_read(plaintext):
        return None
    vals = find_varint(plaintext, ALARM_ARMED_FIELD)
    if not vals:
        return None
    return any(v in (1, 2) for v in vals)

def system_status(plaintext):
    """f27 -> {'led','grade2','satel'} or None.
    f27.f1.f6==1 LED on; f27.f1.f26==1 GRADE2 on; f27.f1.f22==1 SATEL OFF."""
    d = pb_read(plaintext)
    if 27 not in d: return None
    f1 = pb_read(d[27][0])
    if 1 not in f1: return None
    body = pb_read(f1[1][0])
    return {"led": bool(body.get(6) and body[6][0] == 1),
            "grade2": bool(body.get(26) and body[26][0] == 1),
            "satel": not (22 in body and body[22][0] == 1)}

# SystemInfo.Details.HubErrorCode (hub troubles, f27.f1.f1 field 12; memory = field 27)
HUB_ERRORS = {
    0: "tamper", 1: "radio_jammed", 2: "battery_low", 3: "battery_disconnected",
    4: "battery_replace", 5: "ac_power", 6: "ethernet_disconnected", 7: "wifi",
    8: "sim1_missing", 9: "sim1_pin", 10: "sim1_puk", 11: "sim2_missing", 12: "sim2_pin", 13: "sim2_puk",
    14: "sd_missing", 15: "sd_low_memory", 16: "sd_format", 17: "sd_error",
    40: "satel_cloud", 41: "invalid_dc", 42: "charger_fault", 43: "hub_overheat", 44: "event_buffer_full",
    45: "aps_communication", 46: "aps_overload", 47: "aps_overheat", 48: "vcc_output_overload",
    **{c: "reporting_trouble" for c in range(20, 36)},
}
# HubDeviceState.ErrorCode (per device, HubDeviceState field 1)
DEV_ERRORS = {
    0: "connection_failure", 1: "connection_disconnected", 2: "tamper", 3: "sensor_dirty",
    4: "battery_low", 5: "output_overload", 6: "aps_trouble", 7: "aps_overload", 8: "aps_overheat",
    9: "battery_disconnected", 10: "battery_replace", 11: "ac_power", 12: "masking", 13: "fault",
    14: "overheat", 15: "sensor_fault", 16: "smoke_chamber_dirty", 17: "internal_fault",
    18: "ac_sync", 19: "overload", 20: "no_load", 21: "hardware_fault", 22: "calibration_required",
    23: "calibration_problem", 24: "device_inverted", 25: "valve_dc_start", 26: "valve_over_range",
    27: "valve_button", 28: "valve_motor", 29: "valve_internal", 30: "valve_calibration",
    31: "sensor_detection",
}
HUB_TAMPER = 0
DEV_TAMPER = 2


def packed_ints(vals):
    """repeated enum: packed (bytes) or not (ints) -> [int]."""
    out = []
    for v in vals or []:
        if isinstance(v, int):
            out.append(v)
        elif isinstance(v, (bytes, bytearray)):
            i = 0
            while i < len(v):
                x, i = _rv(v, i); out.append(x)
    return out


def hub_info(plaintext):
    """f27 -> {'power','stor_free','stor_total','firmware'} or None."""
    d = pb_read(plaintext)
    if 27 not in d: return None
    f1 = pb_read(d[27][0])
    if 1 not in f1: return None
    body = pb_read(f1[1][0])
    pw = body.get(9, [None])[0]
    if isinstance(pw, int) and pw > 100: pw = None       # fw 1.04: -1 on mains
    fw = None
    if 40 in body:
        v = pb_read(body[40][0]).get(9, [b""])[0]
        if _looks_str(v): fw = v.decode("latin1")
    return {"power": pw, "stor_free": body.get(10, [None])[0],
            "stor_total": body.get(11, [None])[0], "firmware": fw,
            "errors": packed_ints(body.get(12)), "errors_memory": packed_ints(body.get(27))}

def network_active(plaintext):
    """f31 -> active connection name (e.g. 'LAN') or None."""
    d = pb_read(plaintext)
    if 31 not in d: return None
    top = pb_read(d[31][0])
    if 1 not in top: return None
    f1 = pb_read(top[1][0])
    for e in f1.get(1, []):
        ed = pb_read(e)
        if ed.get(5):
            if pb_read(ed[5][0]).get(2, [0])[0] == 1:
                return _s(ed.get(2, [b''])[0])
    return None

def _telem_from_block(f3block, f2list, errs=None):
    """extract telemetry from a device's f3 body + nested f2 (temp) + f1 error codes."""
    f3 = pb_read(f3block) if f3block else {}
    temp = None
    for t in f2list:
        td = pb_read(t)
        if td.get(1, [None])[0] == 6 and 3 in td and isinstance(td[3][0], int):
            temp = _f32(td[3][0])
    return {"signal": f3.get(3, [None])[0], "battery": f3.get(4, [None])[0],
            "state": f3.get(9, [None])[0], "armed": f3.get(20, [None])[0],
            "type": f3.get(10, [None])[0], "temp": temp,
            # HubDeviceState.Settings.onOff (f5); proto3 omits 0 -> off when block present
            "on_off": (f3.get(5, [0])[0] == 1) if f3 else None,
            "volt": _f32(f3[110][0]) if 110 in f3 else None,
            "heater": _heater(f3),
            # HubDeviceState.Settings.keypadBlock (f112): keypad locked by the hub
            "keypad_block": (f3.get(112, [0])[0] == 1) if f3 else None,
            # f1 = packed list of supported HubDeviceState_Settings_Command values
            "options": packed_ints(f3.get(1)) if f3 and 1 in f3 else None,
            "raw": ({k: v[0] for k, v in f3.items() if k != 1 and isinstance(v[0], int)}
                    | {k: _s(v[0]) for k, v in f3.items() if k in (50, 118) and isinstance(v[0], bytes)}) if f3 else None,
            "errors": packed_ints(errs) if errs is not None else None}


def _heater(f3):
    """HubDeviceState.Settings heater fields (ART-210), None for other devices."""
    if 60 not in f3 and 61 not in f3:
        return None
    def flt(k):
        return _f32(f3[k][0]) if k in f3 and isinstance(f3[k][0], int) else None
    return {"mode": f3.get(60, [0])[0], "target": flt(61), "day": flt(62), "night": flt(63),
            "eco": flt(64), "manual": flt(95), "boost": f3.get(97, [0])[0] == 1,
            "boost_time": f3.get(65, [None])[0], "parental": f3.get(68, [0])[0] == 1,
            "valve": f3.get(109, [None])[0], "sensor": f3.get(67, [None])[0],
            "offset": flt(66) if 66 in f3 else 0.0,
            "schedule": _s(f3[50][0]) if 50 in f3 and isinstance(f3[50][0], bytes) else "",
            # heaterSensorSource f118 {f1 = device id}: also 64-bit ids (LUNA bus detectors) that f67 cannot hold
            "sensor_source": _src(f3)}


def _src(f3):
    try:
        v = f3.get(118, [b""])[0]
        return pb_read(v).get(1, [None])[0] if isinstance(v, bytes) and v else None
    except Exception:  # noqa: BLE001
        return None

# --- heating schedules (StructureGetSchedule f46/f47, StructureUpdateSchedule f50/f51) ---------------
# HubSchedule {id f1 str, name f2 str, entries f3[{day f1 (0 = Monday .. 6 = Sunday), ranges f2[{start f1, end f2}]}]},
# time = {hour f1, minute f2}. A schedule holds only time windows: a head in schedule mode heats to its "day"
# temperature inside a window and to its "night" temperature outside.
SCHEDULE_GET = 46
SCHEDULE_UPDATE = 50
SET_SCHEDULE = 50                # device patch: string = schedule id ("" = none)


def parse_schedules(plaintext):
    """f47 -> list of {id, name, days: {"0": [["08:00", "20:00"], ...], ...}}; None if no list in the message."""
    d = pb_read(plaintext)
    if 47 not in d:
        return None
    body = d[47][-1]
    if not body:
        return None                 # bare change notification without the list
    pl = pb_read(body).get(1)
    if not pl:
        return []
    out = []
    for raw in pb_read(pl[0]).get(1, []):
        sd = pb_read(raw)
        days = {}
        for e in sd.get(3, []):
            ed = pb_read(e)
            day = ed.get(1, [0])[0]
            rs = []
            for r in ed.get(2, []):
                rd = pb_read(r)
                def tm(k):
                    t = pb_read(rd[k][0]) if k in rd else {}
                    return f"{t.get(1, [0])[0]:02d}:{t.get(2, [0])[0]:02d}"
                rs.append([tm(1), tm(2)])
            days.setdefault(str(day), []).extend(rs)
        out.append({"id": _s(sd.get(1, [b""])[0]), "name": _s(sd.get(2, [b""])[0]), "days": days})
    return out


def schedules_plaintext(schedules):
    """Full schedule list -> StructureUpdateSchedule request (the hub replaces its whole list)."""
    def tm(hhmm):
        h, m = (int(x) for x in str(hhmm).split(":"))
        return (pb_varint(1, h) if h else b"") + (pb_varint(2, m) if m else b"")
    items = b""
    for sc in schedules:
        b = pb_bytes(1, sc["id"].encode()) + pb_bytes(2, sc.get("name", "").encode())
        for day in range(7):
            ranges = (sc.get("days") or {}).get(str(day)) or []
            if not ranges:
                continue
            e = pb_varint(1, day) if day else b""
            for a, z in ranges:
                e += pb_bytes(2, pb_bytes(1, tm(a)) + pb_bytes(2, tm(z)))
            b += pb_bytes(3, e)
        items += pb_bytes(1, b)
    return pb_bytes(SCHEDULE_UPDATE, pb_bytes(1, items))


# Hub main-board outputs share device uuid 1 and differ by HubDeviceKey field 2 (O1 = 5, O2 = 6, ...).
# They get a composite id uuid*1000+index (1005, 1006); real devices (uuid >= 1000) keep their uuid.
COMPOSITE = {}   # composite id -> (uuid, index)

def key_to_id(key_bytes):
    k = pb_read(key_bytes)
    uuid, sub = k.get(1, [None])[0], k.get(2, [None])[0]
    if uuid is None or sub is None or uuid >= 1000:
        return uuid
    cid = uuid * 1000 + sub
    COMPOSITE[cid] = (uuid, sub)
    return cid

def key_bytes(devid):
    """HubDeviceKey for a (possibly composite) device id."""
    if devid in COMPOSITE:
        uuid, sub = COMPOSITE[devid]
        return pb_varint(1, uuid) + pb_varint(2, sub)
    return pb_varint(1, devid)

def parse_telemetry(plaintext):
    """f89 -> {devid: telemetry}."""
    d = pb_read(plaintext)
    if 89 not in d: return {}
    top = pb_read(d[89][0])
    if 1 not in top: return {}
    f1 = pb_read(top[1][0]); out = {}
    for dev in f1.get(2, []):
        dd = pb_read(dev)
        if 1 not in dd: continue
        devid = key_to_id(dd[1][0])
        if devid is None: continue
        body = pb_read(dd[2][0]) if 2 in dd else {}
        out[devid] = _telem_from_block(body[3][0] if 3 in body else None, body.get(2, []),
                                       body.get(1, []) if 2 in dd else None)
    return out

def parse_config_devices(plaintext):
    """f45 -> {devid: {name, room, type, model, sysnum, bypass, + telemetry}}."""
    d = pb_read(plaintext)
    if 45 not in d: return {}
    top = pb_read(d[45][0])
    if 1 not in top: return {}
    body = pb_read(top[1][0]); out = {}
    for room in body.get(4, []):
        rd = pb_read(room); rname = _s(rd.get(2, [b''])[0])
        for dev in rd.get(5, []):
            dd = pb_read(dev)
            if 1 not in dd: continue
            devid = key_to_id(dd[1][0])
            if devid is None: continue
            tel = {"type": None}
            if 20 in dd:
                t = pb_read(dd[20][0])
                tel = _telem_from_block(t[3][0] if 3 in t else None, t.get(2, []), t.get(1, []))
            out[devid] = {"name": _s(dd.get(9, [b''])[0]), "room": rname,
                          "type": tel.get("type"),
                          "model": DEV_MODELS.get(dd.get(2, [None])[0]),
                          "hwtype": dd.get(2, [None])[0],
                          # output reaction type (HubDevice f30): SWITCH_BI / SWITCH_MONO (pulse timed by the hub) / …
                          "output_type": {0: "bi", 1: "mono", 2: "thermostat_heating", 3: "thermostat_cooling",
                                          4: "alarm"}.get(dd.get(30, [None])[0]),
                          "on_off": tel.get("on_off"),
                          "heater": tel.get("heater"),
                          "sysnum": dd.get(21, [None])[0],
                          "bypass": bool(dd.get(22, [0])[0]),
                          "signal": tel.get("signal"), "battery": tel.get("battery"),
                          "state": tel.get("state"), "temp": tel.get("temp"),
                          "volt": tel.get("volt"), "errors": tel.get("errors"),
                          "keypad_block": tel.get("keypad_block"),
                          "options": tel.get("options"), "raw": tel.get("raw")}
    return out

# ---------- framing ----------
def build_message(counter, plaintext, key, aad, user_id=2, device_id=4, with_ids=True):
    ts = int(time.time()) & 0xffffffff; rnd = secrets.randbits(31)
    ids = (pb_varint(3, user_id) + pb_varint(4, device_id)) if with_ids else b''
    header = (pb_fixed32(2, len(plaintext)) + ids + pb_varint(5, counter)
              + pb_varint(6, ts) + pb_varint(7, rnd) + pb_bytes(8, b'\x01\x00'))
    iv = struct.pack('>III', rnd, ts, counter)
    ct = AESGCM(key).encrypt(iv, plaintext, aad)
    return b'\x0a' + _wv(len(header)) + header + ct

def parse_header(buf, i):
    if buf[i] != 0x0a: return None
    try: hlen, j = _rv(buf, i + 1)
    except IndexError: return None
    if j + hlen > len(buf): return None
    header = buf[j:j+hlen]; fd = {}; k = 0
    try:
        while k < len(header):
            tag, k = _rv(header, k); fn, wt = tag >> 3, tag & 7
            if wt == 5:
                if k + 4 > len(header): return None
                fd[fn] = struct.unpack_from('<I', header, k)[0]; k += 4
            elif wt == 0: fd[fn], k = _rv(header, k)
            elif wt == 1:
                if k + 8 > len(header): return None
                fd[fn] = struct.unpack_from('<Q', header, k)[0]; k += 8
            elif wt == 2:
                ln, k = _rv(header, k)
                if k + ln > len(header): return None
                fd[fn] = header[k:k+ln]; k += ln
            else: return None
    except Exception:
        return None
    return fd, hlen, j + hlen

# ============================================================
class BeWaveError(Exception): pass
class BeWaveAuthError(BeWaveError):
    """Hub rejected the login / password (header error 102 during sign-in)."""
class BeWaveDeviceLimitError(BeWaveError):
    """Hub refused a new mobile-device token: per-user slot limit reached (header error 31)."""

# header f10 error codes seen during sign-in
SIGNIN_ERR_AUTH = 102
SIGNIN_ERR_DEVICE_LIMIT = 31

class BeWaveClient:
    def __init__(self, login, password, device_uuid=None, device_token=None):
        self.login = login
        self.password = password
        self.uuid = (device_uuid or secrets.token_hex(16).upper())[:32]
        self.token = device_token or ("ha-bewave-" + self.uuid)
        self.serial = None
        self.session_id = None
        self.session_secret = None
        # user/device ids are assigned by the hub (sign-in response header f3/f4);
        # Smart HUB demo happened to use 2/4, HYBRID assigns its own device id.
        self.user_id = 2
        self.device_id = 4
        self._counter = 0
        self.K_signin = md5(password) * 2
        self.A_signin = login.encode()
        self.A_resp = md5(pad(login, 64))

    def next_counter(self):
        self._counter += 1
        return self._counter
    def signin_message(self):
        # Sign in like the Be Wave app: credentials + deviceToken + hubLocalIdentifier.
        # A device token first registered WITH credentials gets full device control
        # (DeviceStateUpdate); one registered token-only is silently read-only there.
        creds = pb_bytes(2, self.login.encode()) + pb_bytes(3, self.password.encode())
        inner = (pb_bytes(1, creds) + pb_bytes(2, self.uuid.encode())
                 + pb_bytes(3, (self.serial or "").encode()))
        return build_message(self.next_counter(), pb_bytes(6, pb_bytes(1, inner)),
                             self.K_signin, self.A_signin, with_ids=False)
    def command_message(self, plaintext):
        if not self.session_id:
            raise BeWaveError("not signed in")
        return build_message(self.next_counter(), plaintext, self.uuid.encode(),
                             md5(self.session_id), self.user_id, self.device_id)
    def subscribe_plaintext(self, field): return pb_bytes(field, pb_bytes(1, b""))
    def register_plaintext(self):
        return pb_bytes(8, pb_bytes(1, pb_bytes(1, self.token.encode()) + pb_varint(2, 1)))
    def arm_disarm_plaintext(self, arm, mode="defau"):
        inner = pb_bytes(1, mode.encode()) + pb_varint(2, 1 if arm else 0)
        return pb_bytes(94, pb_bytes(1, pb_bytes(1, inner)))
    def set_on_off_plaintext(self, devid, on):
        """DeviceStateUpdate (f66) -> payload.patches[{deviceId{uuid}, type=SET_ON_OFF, boolean}]."""
        patch = (pb_bytes(1, key_bytes(devid)) + pb_varint(2, SET_ON_OFF)
                 + pb_varint(10, 1 if on else 0))
        return pb_bytes(66, pb_bytes(1, pb_bytes(1, patch)))
    def patch_plaintext(self, devid, command, boolean=None, number=None, flt=None, string=None):
        """Generic DeviceStateUpdate patch for one device."""
        patch = pb_bytes(1, key_bytes(devid)) + pb_varint(2, command)
        if boolean is not None:
            patch += pb_varint(10, 1 if boolean else 0)
        if number is not None:
            patch += pb_varint(11, int(number))
        if string is not None:
            patch += pb_bytes(12, str(string).encode())
        if flt is not None:
            patch += pb_fixed32(13, struct.unpack('<I', struct.pack('<f', float(flt)))[0])
        return pb_bytes(66, pb_bytes(1, pb_bytes(1, patch)))
    def set_name_plaintext(self, name):
        """AccountSetMobileDeviceName (f126) so the device is recognisable in the app."""
        pl = pb_varint(1, self.user_id) + pb_varint(2, self.device_id) + pb_bytes(3, name.encode())
        return pb_bytes(126, pb_bytes(1, pl))
    def setting_toggle_plaintext(self, setting_id):
        return pb_bytes(34, pb_bytes(1, pb_varint(1, setting_id) + pb_bytes(2, pb_bytes(3, b""))))

    def decrypt(self, buf, i):
        parsed = parse_header(buf, i)
        if not parsed: return None
        fd, hlen, off = parsed
        clen = fd.get(2, 0); ct = buf[off:off+clen]; tag = buf[off+clen:off+clen+16]
        if len(tag) < 16: return None
        iv = struct.pack('>III', fd.get(7, 0) & 0xffffffff,
                         fd.get(6, 0) & 0xffffffff, fd.get(5, 0) & 0xffffffff)
        end = off + clen + 16
        for key, aad in self._decrypt_keys():
            try:
                return fd, AESGCM(key).decrypt(iv, ct + tag, aad), end
            except Exception:
                continue
        return fd, None, end
    def _decrypt_keys(self):
        ks = [(self.uuid.encode(), self.A_resp)]
        if self.session_secret and self.session_id:
            ks.append((self.session_secret, md5(self.session_id)))
            ks.append((self.uuid.encode(), md5(self.session_id)))
        ks.append((self.K_signin, self.A_signin))
        return ks
    def ingest_response(self, plaintext):
        try:
            l2 = pb_read(pb_read(pb_read(plaintext)[7][0])[1][0])
            self.serial = l2[2][0].decode('latin1')
            self.session_id = l2[3][0].decode('latin1')
            self.session_secret = l2[4][0]
            return True
        except Exception:
            return False


class LocalConnection:
    def __init__(self, host, client, port=DATA_PORT, timeout=8):
        self.host = host; self.port = port; self.client = client; self.timeout = timeout
        self.sock = None; self._buf = b''

    def connect_and_signin(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)
        self.sock.sendall(self.client.signin_message())
        deadline = time.time() + self.timeout; signed = False
        while time.time() < deadline and not signed:
            self._pump()
            for fd, pt in self._drain():
                err = fd.get(10)
                if err == SIGNIN_ERR_AUTH:
                    raise BeWaveAuthError("invalid login or password")
                if err == SIGNIN_ERR_DEVICE_LIMIT:
                    raise BeWaveDeviceLimitError("mobile-device limit for this user reached")
                if pt and len(pt) > 200 and self.client.ingest_response(pt):
                    if isinstance(fd.get(3), int):
                        self.client.user_id = fd[3]
                    if isinstance(fd.get(4), int):
                        self.client.device_id = fd[4]
                    signed = True; break
        if not signed:
            raise BeWaveError("sign-in response not received/decrypted")
        for f in STATE_SUBS:
            self.sock.sendall(self.client.command_message(self.client.subscribe_plaintext(f)))
            time.sleep(0.02)
        try:
            self.sock.sendall(self.client.command_message(self.client.register_plaintext()))
        except Exception:
            pass
        # capture the hub's initial config (f45) + per-device telemetry (f89) burst,
        # which is pushed once right after subscribing. The standalone app catches it
        # via its continuous reader; here we read a few seconds so the first poll parses it.
        # Short socket timeout from now on so readers never block past the hub's
        # idle limit; keep the session alive with refreshes while we wait.
        self.sock.settimeout(0.5)
        t0 = last = time.time()
        while time.time() - t0 < 3.5:
            if time.time() - last >= KEEPALIVE_S:
                self.refresh(); last = time.time()
            self._pump()
        return True

    def refresh(self):
        """Light re-subscribe (f88 device state, f26 settings) — call each poll."""
        for f in REFRESH_SUBS:
            try:
                self.sock.sendall(self.client.command_message(self.client.subscribe_plaintext(f)))
            except Exception:
                pass

    def _pump(self):
        try:
            data = self.sock.recv(8192)
            if data:
                self._buf += data
            else:
                # recv() returning b'' means the hub closed the socket (it allows
                # only a few concurrent sessions; opening the phone/web app evicts
                # this one). Surface it so the caller reconnects instead of looping.
                raise BeWaveError("connection closed by hub")
        except socket.timeout:
            pass
    def _drain(self):
        out = []; i = 0
        while i < len(self._buf):
            if self._buf[i] != 0x0a:
                i += 1; continue
            res = self.client.decrypt(self._buf, i)
            if not res: break
            fd, pt, end = res
            if end > len(self._buf): break
            out.append((fd, pt)); i = end
        self._buf = self._buf[i:]
        if len(self._buf) > 200000:    # desync safety
            self._buf = b""
        return out
    def send_command(self, plaintext):
        self.sock.sendall(self.client.command_message(plaintext))
    def arm(self, mode="defau"):  self.send_command(self.client.arm_disarm_plaintext(True, mode))
    def disarm(self, mode="defau"): self.send_command(self.client.arm_disarm_plaintext(False, mode))
    def toggle_setting(self, setting_id): self.send_command(self.client.setting_toggle_plaintext(setting_id))
    def set_on_off(self, devid, on): self.send_command(self.client.set_on_off_plaintext(devid, on))
    def set_name(self, name): self.send_command(self.client.set_name_plaintext(name))
    def send_patch(self, devid, command, **kw): self.send_command(self.client.patch_plaintext(devid, command, **kw))
    def read_messages(self, seconds=2):
        out = []; t0 = time.time()
        while time.time() - t0 < seconds:
            self._pump(); out += self._drain()
        return out
    def close(self):
        try: self.sock.close()
        except Exception: pass


def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["status", "arm", "disarm"])
    ap.add_argument("--host"); ap.add_argument("--login"); ap.add_argument("--password")
    ap.add_argument("--serial"); ap.add_argument("--uuid"); ap.add_argument("--mode", default="defau")
    a = ap.parse_args()
    c = BeWaveClient(a.login, a.password, a.uuid)
    if a.serial: c.serial = a.serial
    conn = LocalConnection(a.host, c)
    conn.connect_and_signin()
    conn.close(); time.sleep(1)         # register, then reconnect as a known device
    conn = LocalConnection(a.host, c); conn.connect_and_signin()
    print("signed in. serial=", c.serial)
    if a.action == "arm": conn.arm(a.mode); print("ARM sent")
    elif a.action == "disarm": conn.disarm(a.mode); print("DISARM sent")
    armed = None
    for _fd, pt in conn.read_messages(3):
        if pt is not None:
            st = armed_state(pt)
            if st is not None: armed = st
    print("state:", {True: "ARMED", False: "DISARMED"}.get(armed, "unknown"))
    conn.close()

if __name__ == "__main__":
    _cli()
