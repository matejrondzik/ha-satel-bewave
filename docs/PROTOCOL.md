# BE WAVE local protocol — notes

These are working notes for contributors. They are incomplete and not official. Everything
was observed against hubs owned by the authors. The implementation lives in
[`custom_components/bewave/bewave_client.py`](../custom_components/bewave/bewave_client.py),
which also works as a stand-alone CLI (`python3 bewave_client.py status --host … --login … --password …`).

## Transport

- Discovery: **UDP 4111**, plaintext (no keys). Broadcast a `TransportMessage` with only `SystemPing`
  (`24 {1 {}}`, no header needed); every hub answers with `25 {1 Details}`: localIdentifier 1 (= the serial
  used at sign-in), connectionParameters 2 (`"ip:4200"`), noUsers 3, name 4, timezoneSet 5, interfacesSet 6,
  deviceDescription 7 (… 7 radio band 868, 9 firmware `"1.05"`). The ping changes nothing.
- Session: **TCP 4200**.
- Frame: `0x0a` + protobuf header + AES-256-GCM ciphertext of a protobuf `TransportMessage`.
- Header fields: contentLength 2, userId 3, deviceId 4, counter 5, timestamp 6, random 7,
  protocolVersion 8, status 9, **error 10**.

| Direction | Key | AAD |
|---|---|---|
| sign-in app → hub | MD5(password) ×2 | login |
| sign-in hub → app | deviceUuid (32 ASCII bytes) | MD5(login padded to 64) |
| commands app → hub | deviceUuid | MD5(session token) |
| push hub → app | session secret | MD5(session token) |

**Error codes:** 31 = mobile-device limit for this user reached, 102 = wrong login/password,
100/101 = malformed header.

The HYBRID assigns its own `userId`/`deviceId` in the sign-in response header and must be
used from then on. It also closes idle sessions after ~2.7 s, so send a keepalive every ~1 s.

## Messages (request even, response = request + 1)

| # | Message | Notes |
|---|---|---|
| 2 | AccountGet | users and their mobile devices |
| 6 | AccountSignIn | credentials {login 2, password 3}, deviceToken 2, hubLocalIdentifier 3 |
| 8 | AccountConfigureNotifications | push-token registration (the app sends it after sign-in) |
| 24 | SystemPing | keepalive |
| 26 | SystemInfo | features 25, connections 7, cloudConnectionDisabled 22; status block 27 → troubles (see below) |
| 30 | NetworkInterfaces | |
| 32 | SystemEvent | event history. Request is **flat**: `32 {1 {1 page (from 1), 2 page size, 5 language}}` → `{1 [HubEvent], 2 total}`. HubEvent: id 1, timestamp 2, type 3 (0 controls, 1 alarms, 2 failures, 3 others, 4 arming, 5 Grade 2, 6 video), action 4, message 5 (readable text), user / mode name 6, source 7 |
| 34 | SystemTrigger | 0 FW check, 4 time sync, 5 status LED, 11 cloud, 12 Grade 2. **1 FW update, 2 restart, 3 factory reset: never send.** |
| 44 | StructureGet | rooms, devices, configuration (response 45) |
| 46 | StructureGetSchedule | heating schedules (response 47) |
| 50 | StructureUpdateSchedule | replace the whole schedule list (response 51) |
| 64 | DeviceStateGetChanges | measurement history only |
| 66 | DeviceStateUpdate | device commands (below); the response (67) is empty even on success, so verify via state |
| 88 | SystemMessage | pushed state changes (response 89) |
| 94 | StructureUpdateAlarmState | arm/disarm: patches {id 1, enabled 2} |
| 104 | StructureUpdateDevice | change a device's configuration (e.g. output type). **Rejected on the integration's session** (the hub closes it) - use the app |
| 112 / 122 / 124 | keypad code / NFC cards / read NFC card | not used - do not edit from HA |
| 126 | AccountSetMobileDeviceName | {userId, deviceId, name} - shown in the app's device list |
| 132 / 134 | SystemGet/SetKeypadSettings | `132 {1 {1 HubDeviceKey}}` → keypad model, language, screens, layout. Never set without a reason |

The integration subscribes to `88, 44, 26, 52, 46, 56, 58, 90, 24, 30` once after sign-in and refreshes with
`88, 26`. **Subscribing to 44 / 46 again in a live session makes the hub close it.**

The panel has **no MQTT**. Port **5000** accepts the raw text inputs of virtual IP devices (`ipZoneConfig`).

A device token registered **with credentials** (like the app does) may send
DeviceStateUpdate. A token registered token-only is silently read-only for device control.

## DeviceStateUpdate (66)

`payload 1 { patches 1 [ { deviceId 1 = {uuid 1, channel 2}, type 2 = command, value } ] }`
with the value in `boolean 10`, `number 11`, `string 12` or `float 13` (fixed32).

| Command | Value | Meaning |
|---|---|---|
| 1 SET_ON_OFF | bool | outputs, virtual IP devices, LUNA light ring |
| 2 BLOCK | bool | block / unblock a device (e.g. keypad) |
| 3 OPEN_CLOSE | | |
| 20 ALARM_OPTICAL | bool | LUNA optical signalling |
| 40 / 41 | number | shutter position / tilt (untested) |
| 60 HEATER_WORKING_MODE | number | 0 manual, 1 schedule, 2 eco |
| 61 HEATER_FORCED_VALUE | float | current target temperature |
| 62 / 63 / 64 | float | day / night / eco temperature |
| 104 | float | manual temperature |
| 107 HEATER_BOOST | bool | boost |
| 21 ALARM_OPTICAL_DURATION | number | LUNA light-on time after motion, s (0–180) |
| 50 SET_SCHEDULE | string | schedule id of a head (`""` = none) |
| 65 | number | boost time, ms |
| 66 | float | temperature offset (heads and detectors) |
| 67 | number | reference sensor of a head (uint32 only - 64-bit BUS detector ids do not fit, see 118 below) |
| 68 | bool | child lock of a head |
| 30–34 | | dimmer: brightening value / time, control, display, memory (untested) |
| 115 / 116 | | keypad NFC short / hold configuration |
| 117 | float | thermostat hysteresis (untested) |
| 124 FIND_ME | – | identify device |
| 125 | | edit graphic keypad settings |
| 126 SET_DURATION_TIME | number | pulse length of a mono output (unit unknown, set it in the app) |

**The hub drops a command that arrives within ~1 s of the previous one**, and closes the session on a
request that bundles several patches. Send one patch per message, ≥ 1.5 s apart, and verify the state.

The device state (`HubDeviceState.Settings`, in telemetry 89 and in config 45 → device 20) lists the
commands it supports in field **1** (packed) - the most useful field for recognising capabilities.
Other state fields:

| Field | Meaning |
|---|---|
| 3 / 4 | signal % / battery % |
| 5 | on/off (proto3: missing = off) |
| 6 / 7 | block / open-close |
| 9 | violation: motion, contact open, water, zone input |
| 10 | telemetry type (`DEV_TYPES`), for keypads the firmware version |
| 20 / 21 | alarm armed / detected (keypad) |
| 22 / 23 | LUNA optical signalling / light-on time (s) |
| 50 | schedule id of a head (string) |
| 60–68 | head: mode, target (61), day, night, eco, boost time (ms), offset, reference sensor, child lock |
| 94 / 96 | LUNA brightness min / max |
| 95 / 97 | head manual temperature / boost active |
| 107 / 108 | keypad entry / exit delay countdown |
| 109 | valve opening % |
| 110 | supply voltage (float) |
| 112 | keypad locked by the hub (too many wrong codes) |
| 114–116 | keypad NFC card short / hold / id (string) |
| 118 | head reference sensor `{1: device id}` - also holds 64-bit ids (BUS detectors) |
| 121 | keypad chime |

Temperatures arrive as measurements `{1: 6, 3: float}` in body field 2. Floats are fixed32 (IEEE 754 in an int).

## Device configuration (StructureGet 45)

`HubDevice`: key 1, **type 2** (`HubDeviceType`, see `DEV_MODELS`), category 4, modeOfOperation 8, name 9,
state 20, order number 21 (*system number* in the app), bypass 22, ipZoneConfig 26 (virtual IP devices:
enabled 1, violation time 2, HTTP request 4, output on 6 / off 7, ip 8, port 9), inputReactionType 28,
**outputReactionType 30** (0 bi, 1 mono, 2 thermostat heating, 3 thermostat cooling, 4 alarm). Rooms: 4 [{name 2,
devices 5}]. Firmware 1.05 also sends fields 33 / 34 that the app 1.3.0 does not know.

## Device types

Full list in `DEV_MODELS` (`bewave_client.py`). Verified on hardware: 1 hub main board (outputs, zones),
11 APD-200, 14 APMD-250, 18 AFD-200 flood, 26 APT-210 key fob, 35 **ART-210** head, 59 SLIM-PIR-LUNA-BUS,
69 **virtual IP device**, 72 TSG2R keypad. LUNA types (light ring): 58, 59, 61, 62, 79, 80.
Keypads: 41, 53, 54, 55, 71, 72, 73, 74.

Telemetry type (state field 10): 0x10002 key fob, 0x10003 motion, 0x10007 contact, 0x1004F / 0x10051
wired BUS detectors (motion). Flood detectors report the generic 0x10001 - recognise them by `HubDeviceType`.

## How the protocol was found

The Be Wave Android app (1.3.0) is React Native with Hermes bytecode (v96). Field numbers come from the
generated ts-proto encoders in the disassembled bundle (message and enum names are kept as strings); the
Hermes opcode table must match the React Native branch the app was built with (`rn/0.79-stable`).
Everything was then confirmed against real hubs.

## Troubles (error codes)

- **Hub:** `SystemInfo.Details` (status block f27 → f1 → f1) field **12** = active `HubErrorCode`s
  (packed), field **27** = troubles memory. 0 TAMPER, 1 RADIO_JAMMED, 2 BATTERY_LOW, 5 PROBLEM_WITH_AC,
  6 ETHERNET_CABLE_DISCONNECTED, 7 PROBLEM_WITH_WIFI, 40 PROBLEM_WITH_SATEL_CLOUD, … (see `HUB_ERRORS`).
- **Device:** `HubDeviceState` field **1** = `HubDeviceState.ErrorCode`s (packed): 0 CONNECTION_FAILURE,
  2 TAMPER, 4 BATTERY_LOW, 12 MASKING, 26 DEV_ART_VALVE_OVER_RANGE, … (see `DEV_ERRORS`).

## Hub main board: outputs and wired zones

Outputs (O1, O2, …) and wired zone inputs (Z1, Z2, …) of the panel's main board share the device
uuid **1** and differ by `HubDeviceKey` field **2** (index): zones Z1 = 1, Z2 = 2; outputs O1 = 5,
O2 = 6. A patch must carry both fields, otherwise the hub ignores it or drops the session. The
integration uses composite ids `uuid * 1000 + index` (1001, 1002, 1005, 1006).

- **Outputs**: options contain 1 SET_ON_OFF; device config field **30** = reaction type
  0 SWITCH_BI, 1 SWITCH_MONO (pulse timed by the hub), 2/3 thermostat heating/cooling, 4 alarm.
- **Zones**: options contain **110**; state field 9 = violated. The zone type (smoke, contact, …) is
  not exposed, so the user sets the device class in Home Assistant.

## Heating schedules

`StructureGetSchedule` (subscribe 46) → field **47** `{1 {1 [HubSchedule]}}`;
`StructureUpdateSchedule` field **50** with the **whole** list replaces it (reply 51).
`HubSchedule {1 id (string), 2 name, 3 [entry {1 day (0 = Monday, omitted for 0), 2 [range {1 start {1 h, 2 m}, 2 end {…}}]}]}`.
A schedule has no temperatures: inside a window a head in schedule mode heats to its day
temperature, outside to the night one. Heads reference a schedule by patch 50 (string id).

## Never send

`SystemTrigger` (fields 1–3: restart, factory reset, firmware update). The integration never sends them.
