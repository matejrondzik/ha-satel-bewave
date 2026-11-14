# Changelog

## 0.9.0
- Scenes and routines stored in the hub: sensor "Scenes and routines" and services `bewave.save_scene`, `bewave.delete_scene`, `bewave.run_scene` (they run in the hub, also without Home Assistant or mains power). Arming, disarming and alarm actions are refused.

## 0.8.0

- **Finds hubs on the network** like the app (UDP 4111 broadcast): the setup lists hubs with name and
  firmware, new hubs appear under *Discovered*, and a configured hub that changed its IP is updated.
- **Wired zones**: the setup asks what is connected (Repairs after setup, Configure → Wired zones) and sets
  the device class; options are now a menu (General / Wired zones).
- Integration type `hub`.
- Docs for AI assistants (`CLAUDE.md`, `AGENTS.md`, `docs/AI_SETUP.md`), `docs/FINDINGS.md` (hub behaviour),
  `docs/ADDING_DEVICES.md`, extended `docs/PROTOCOL.md`, issue templates.

## 0.7.0

- **Panel outputs** (O1, O2, …) as switches, with `output_type` (bi / mono pulse).
- **Wired zone inputs** (Z1, Z2, …) as binary sensors (set the device class in HA, e.g. smoke or garage door).
- **Flood detectors** (AFD-200) as moisture sensors.
- **SLIM LUNA** detectors: light ring (`light`) and light-on time (`number`).
- **Thermostat heads**: day / night / eco / manual temperature, offset and boost time as numbers;
  target temperature in 0.1 °C; room temperature from the head's reference sensor; schedule name.
- **Detector temperature offset** (`number`).
- **Heating schedules**: services `bewave.save_schedule`, `bewave.delete_schedule`, `bewave.assign_schedule`.
- Composite device keys for main-board I/O (fixes O1/O2 merging into one device).
- 1.5 s gap between commands (the hub drops back-to-back commands).
- Developer services `bewave.debug_patch` / `bewave.debug_send`, off by default (integration options).
- Translated entity names and error messages (EN, SK); protocol tests and CI.

## 0.6.0

- HACS packaging, reworked setup flow (reauth, reconfigure, options, diagnostics), brand icon.
- Thermostat heads (climate), whole-house heating mode, tamper / trouble sensors, keypad lockout,
  device models, signal and voltage diagnostics.
