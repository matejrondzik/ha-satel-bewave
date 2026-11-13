# Adding support for a new device

For people and AI assistants. Read [`FINDINGS.md`](FINDINGS.md) first - most surprises are explained there.

A new BE WAVE device usually **already appears** in Home Assistant with tamper / trouble sensors, signal and
voltage, because the integration creates those for every device. What is missing is its main function
(e.g. a dimmer level, a shutter position). The steps below find the fields and commands for it.

## 1. Collect data (no risk)

1. Add the device in the Be Wave app and name it.
2. In Home Assistant: **Settings → Devices & services → BE WAVE → ⋮ → Download diagnostics.** Names,
   serial and credentials are removed. Find the device in `hub.devices`:
   - `hwtype` - device type (`DEV_MODELS` in `bewave_client.py`); `model` is `null` if unknown,
   - `cat` - category from the telemetry type,
   - `options` - **commands the device accepts** (numbers from the command table in `PROTOCOL.md`),
   - `raw` - every integer state field. Change something in the app (switch, move, trigger the detector),
     download diagnostics again and compare `raw` - the changed field is the one you need.
3. If you only want the device supported, open an issue with both diagnostics files and what you changed
   between them (*New device* issue template). That is enough for a maintainer.

## 2. Try a command (developer services)

Only on your own system, and only with commands from the device's `options`.

1. **Settings → Devices & services → BE WAVE → Configure → Developer services** (on).
2. `bewave.debug_patch` with `device` (id from the diagnostics before anonymisation = attribute `serial`
   or `device_id` of its entities), `command` and one value (`boolean`, `number`, `flt` or `string`).
   The response shows the raw state before and after.
3. One patch at a time, wait for the result. If the hub closes the session the integration reconnects by
   itself; do not repeat the same command in a loop.
4. **Never** use `bewave.debug_send` with messages you do not understand. `SystemTrigger` (34) values 1-3
   update firmware, restart or factory-reset the hub.
5. Turn developer services off again.

## 3. Write the code

| What | Where |
|---|---|
| Model name for a new `hwtype` | `DEV_MODELS` in `bewave_client.py` |
| New command id | `SET_*` constants in `bewave_client.py` (+ table in `PROTOCOL.md`) |
| New state field in parsed data | `_telem_from_block()` (all integers are already in `raw`) |
| Entity | the platform file: add a condition to its `_sync()` - prefer **capabilities** (`command in d["options"]`) over model lists, so similar devices work too |
| Name | `_attr_translation_key` + `strings.json`, `translations/en.json` (same), `translations/sk.json` |
| Sending | `hub.set_patch(devid, command, number=…/boolean=…/flt=…/string=…, verify=lambda: …)` in an executor job |

Rules:

- `unique_id` = `bewave_{serial}_{device_id}_{suffix}`; never change existing ones.
- Pass `verify` so the command is retried / reported when the hub drops it.
- Composite ids (main-board I/O) are handled by `key_bytes()` - just pass the device id.
- Add a test in `tests/test_protocol.py` for new parsing helpers.
- Update `README.md` (supported devices), `PROTOCOL.md`, `CHANGELOG.md`.

## 4. Test on hardware and open a pull request

- Deploy `custom_components/bewave/` to Home Assistant and **restart** (a reload does not load new code).
- Check that existing entities keep their ids and that the new entity follows changes made in the app.
- Say in the PR what you verified physically and what you could not (e.g. smoke, water, a gate).
