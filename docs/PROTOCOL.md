# BE WAVE local protocol — notes

These are working notes for contributors. They are incomplete and not official. Everything
was observed against hubs owned by the authors. The implementation lives in
[`custom_components/bewave/bewave_client.py`](../custom_components/bewave/bewave_client.py),
which also works as a stand-alone CLI (`python3 bewave_client.py status --host … --login … --password …`).

## Transport

- Discovery: UDP 4111. Session: **TCP 4200**.
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
| 6 | AccountSignIn | credentials {login 2, password 3}, deviceToken 2, hubLocalIdentifier 3 |
| 24 | SystemPing | keepalive |
| 26 | SystemInfo | features, connections, cloud flag |
| 34 | SystemTrigger | 4 time sync, 5 status LED, 11 cloud, 12 Grade 2. **1 FW update, 2 restart, 3 factory reset: never send.** |
| 44 | StructureGet | rooms, devices, configuration |
| 66 | DeviceStateUpdate | device commands (below); the response is empty even on success, so verify via state |
| 88 | SystemMessage | pushed state changes (response 89) |
| 94 | StructureUpdateAlarmState | arm/disarm: patches {id 1, enabled 2} |

A device token registered **with credentials** (like the app does) may send
DeviceStateUpdate. A token registered token-only is silently read-only for device control.

## DeviceStateUpdate (66)

`payload 1 { patches 1 [ { deviceId 1 = {uuid 1, channel 2}, type 2 = command, value } ] }`
with the value in `boolean 10`, `number 11`, `string 12` or `float 13` (fixed32).

| Command | Value | Meaning |
|---|---|---|
| 1 SET_ON_OFF | bool | outputs, virtual IP devices |
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
| 124 FIND_ME | – | identify device |

**The hub drops a command that arrives within ~1 s of the previous one**, and closes the session on a
request that bundles several patches. Send one patch per message, ≥ 1.5 s apart, and verify the state.

The device state lists the commands it supports in field 1 (packed). Other state fields:
3 signal, 4 battery, 5 on/off, 9 violation, 60–68 heater, 97 boost, 109 valve opening,
110 voltage (float). Temperatures arrive as measurements `{1: 6, 3: float}`.

## Device types (selection)

14 APMD-250 · 28 ASW-210 · 32 ADC-200 · 35 **ART-210** (thermostat head) · 36 ATPH-200 ·
59 SLIM-PIR-LUNA-BUS · 69 **virtual IP device** · 72 TSG2R keypad

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
