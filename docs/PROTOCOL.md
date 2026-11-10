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
| 124 FIND_ME | – | identify device |

The device state lists the commands it supports in field 1 (packed). Other state fields:
3 signal, 4 battery, 5 on/off, 9 violation, 60–68 heater, 97 boost, 109 valve opening,
110 voltage (float). Temperatures arrive as measurements `{1: 6, 3: float}`.

## Device types (selection)

14 APMD-250 · 28 ASW-210 · 32 ADC-200 · 35 **ART-210** (thermostat head) · 36 ATPH-200 ·
59 SLIM-PIR-LUNA-BUS · 69 **virtual IP device** · 72 TSG2R keypad
