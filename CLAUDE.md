# Instructions for AI assistants (Claude Code and others)

This repository is a Home Assistant custom integration (`custom_components/bewave`, domain `bewave`) for
**Satel BE WAVE** hubs and HYBRID control panels. It talks to the hub on the LAN (TCP 4200) with the
same encrypted protobuf protocol as the Be Wave mobile app. There is no official API: everything was
reverse-engineered, so be careful and verify on real hardware.

Users who only want to **install and configure** the integration: read [`docs/AI_SETUP.md`](docs/AI_SETUP.md).
Mapping a **new device type**: read [`docs/ADDING_DEVICES.md`](docs/ADDING_DEVICES.md).

## Safety rules - always

This code controls an **alarm system**, heating and outputs that may drive gates or boilers.

1. **Never send `SystemTrigger`** messages (top-level fields 1-3: restart, factory reset, firmware update) or
   anything you cannot identify. Never add code that can send them.
2. **Never arm / disarm the alarm, switch outputs, open gates or change heating on a user's system without
   their explicit OK** for that specific action. Reading state is fine.
3. **One patch per message, ≥ 1.5 s apart, then verify the state.** The hub silently drops a command that
   arrives right after another one and closes the session on multi-patch requests. `BeWaveHub._command`
   already enforces this - always go through `hub.set_patch()` / `hub._command()`.
4. **Do not re-subscribe in a live session** (e.g. sending field 44/46 subscribe again): the hub drops the
   connection. Subscriptions happen once in `LocalConnection.connect_and_signin()`.
5. **One session per device token.** The integration keeps its token (`device_uuid` in the config entry).
   Any other client (e.g. the CLI in `bewave_client.py`) must use a **different** uuid, and every new uuid
   uses one of the user's limited *mobile-device slots* on the hub. Do not register tokens in loops.
6. **No personal data in the repo**: no IP addresses of real installations, passwords, tokens, device
   serials, room or person names. Use placeholders (`192.168.1.50`, `123456`, "Living room").
7. Do not tell users to update this integration in a way that overwrites local changes without a backup.

## Layout

| File | Role |
|---|---|
| `bewave_client.py` | Protocol only, **no Home Assistant imports**: framing, AES-GCM, sign-in, protobuf read/write (`pb_*`), parsers (`parse_config_devices`, `parse_telemetry`, `parse_schedules`, `hub_info`), command builders, device type tables (`DEV_MODELS`, `DEV_TYPES`, `LUNA_TYPES`, `KEYPAD_TYPES`, command ids `SET_*`). Runnable as a CLI. |
| `__init__.py` | `BeWaveHub`: session, 1 s keepalive thread, push updates, `devices()` (merged config + telemetry per device), `set_patch`, `set_on_off`, schedules; coordinator; developer services (only with option `developer_services`). |
| `binary_sensor.py`, `switch.py`, `climate.py`, `number.py`, `sensor.py`, `light.py`, `select.py`, `alarm_control_panel.py` | Platforms. Each has a `_sync()` that creates entities for devices as they appear (devices can show up after setup). |
| `config_flow.py` | Setup: UDP discovery (`proto.discover`) → pick hub → login + password; `integration_discovery` (background scan in `__init__.async_setup`, also updates a changed IP), `dhcp`; reauth / reconfigure keep `device_uuid`; options menu (general, wired zones). |
| `zones.py`, `repairs.py` | Wired zone types (`entry.options["zone_classes"]`), shared form; repair issue *zone_types* raised after setup when zones have no type. |
| `errors.py` | Readable trouble descriptions for hub / device error codes. |
| `strings.json`, `translations/en.json`, `translations/sk.json` | **English is the main language** (`strings.json` = `en.json`); Slovak is a translation. Every user-visible text goes here (entity names via `translation_key`, errors via `translation_key` in `HomeAssistantError`, service texts). Keep all three files in sync. |
| `services.yaml` | Only fields and selectors; texts live in the translations. |
| `docs/PROTOCOL.md` | Protocol notes - update when you learn something new. |
| `tests/test_protocol.py` | Tests for `bewave_client.py` (no HA needed). |

### Device model

`hub.devices()` returns dicts like:

```python
{"id": 123456, "name": "Detector", "room": "Living room", "model": "APD-200", "hwtype": 11,
 "cat": "motion",            # from telemetry type f10 (DEV_TYPES): motion / contact / keyfob / device
 "state": 0,                 # f9 violation (motion, contact, water, zone input)
 "temp": 21.5, "battery": 100, "signal": 90, "volt": 3.0,
 "options": [80, 66, 2],     # f1 = commands the device accepts (HubDeviceState_Settings_Command)
 "heater": {...} | None,     # ART-210 fields
 "output_type": "bi" | "mono" | None,
 "errors": [], "raw": {field: int, ...}}   # every integer state field - use it to find new values
```

- `id` is the HubDeviceKey uuid. Main-board I/O shares uuid 1 and is told apart by key field 2:
  composite id `uuid * 1000 + index` (`key_to_id` / `key_bytes`). Never send a patch for such a device
  with only field 1.
- Entity `unique_id`: `bewave_{hub_serial}_{device_id}_{suffix}`. **Never change an existing unique_id**
  (users would lose entity ids, history and helpers bound to them).
- Entities decide what to create from `hwtype`, `cat` and `options` - prefer capabilities (`options`) over
  model lists so similar devices work automatically.

## Working on the code

- Python 3.13, Home Assistant 2025.1+, no external requirements (`cryptography` ships with HA).
- Run tests: `pip install pytest cryptography && pytest -q tests`.
- CI (`.github/workflows/validate.yml`) runs hassfest, HACS validation and the tests. Hassfest rejects
  `{…}` in translation strings unless it is a placeholder (`{name}`).
- Add tests for every new parser / builder in `bewave_client.py`.
- Keep code comments and commit messages in English. Small focused commits.
- Bump `manifest.json` `version` and add a `CHANGELOG.md` entry for user-visible changes. Release = git tag
  `vX.Y.Z` + GitHub release (HACS uses releases).

## Testing on real hardware

There is no simulator. If the user lets you test on their hub:

- Prefer read-only checks (diagnostics, entity states) first.
- Deploy by copying `custom_components/bewave/` to the HA `config/custom_components/bewave/` (backup first)
  and **restart Home Assistant** - reloading the config entry does not reload Python modules.
- Change one thing, verify the state in HA and in the Be Wave app, then continue.
- Things you cannot verify (a physical gate, smoke, water) must be reported as untested.
