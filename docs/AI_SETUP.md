# Set up BE WAVE with Claude (or another AI assistant)

This guide is for people who want an AI assistant to install and configure the integration, and it is also
the **instruction sheet for the assistant**. Give the assistant this file (or the link to it) at the start.

## Ways to work with an assistant

| Setup | What the assistant can do |
|---|---|
| **Chat** (claude.ai, any chat AI) | Explains steps, writes YAML for automations and dashboards; you click and paste. Safest. |
| **Claude Code / an agent with terminal access** | Can read your Home Assistant through the REST / WebSocket API with a long-lived token, check entities, write files over SSH. Give it only what it needs. |
| **Home Assistant MCP Server** (HA integration *Model Context Protocol Server*) | The assistant controls exposed entities through Assist. Expose only what it should touch - **do not expose the alarm panel or gate outputs** unless you want that. |

Create the long-lived token in your HA profile → *Security*. Keep it out of chats you share; revoke it
when you are done.

## Prompts you can paste

**Install**

> Help me install the BE WAVE (Satel) integration from https://github.com/matejrondzik/ha-satel-bewave in my
> Home Assistant. Read docs/AI_SETUP.md in that repository first and follow its rules. My HA version is …,
> I do / do not have HACS. The hub's IP address is …

**Organise devices**

> The BE WAVE integration is set up. List its devices with their `room` attribute, propose Home Assistant
> areas and friendly entity ids (e.g. `binary_sensor.living_room_motion`), and apply them after I confirm.

**Wired zones and outputs**

> My panel has wired zones Z1 (smoke detector) and Z2 (garage door contact) and output O2 drives the garage
> door. Set the right device classes and names, and make a script that opens / closes the door with a
> confirmation in the dashboard. Do not trigger the output yourself.

**Heating**

> Show my thermostat heads, their mode, schedule and reference sensor. Create a schedule "Weekdays" 6-8 and
> 16-22 for the living room heads and tell me which day / night temperatures they will use.

**Safety automations**

> Create automations: push notification when any BE WAVE flood or smoke sensor turns on, when the keypad
> gets locked, and when a device reports tamper. Use my mobile app notify service.

**Boiler from valves**

> My boiler is on panel output O1 (`switch.…`). Turn it on when any thermostat head valve opening is
> ≥ 10 % for 30 s and off when all are below 10 % for 15 s, with at least 15 minutes between off and on.

## Instructions for the assistant

You are helping a user set up a **security system**. Follow these rules:

1. **Ask before any action with a physical effect**: arming / disarming, switching outputs (they may drive a
   gate, a boiler or a siren), changing heating, writing schedules. Reading state is fine. Never trigger a
   gate or door output to "test" it - ask the user to do it while watching.
2. **Never** send raw protocol messages to the hub unless the user explicitly asks to map a new device, and
   then follow [`ADDING_DEVICES.md`](ADDING_DEVICES.md). `SystemTrigger` values 1-3 (firmware update,
   restart, factory reset) must never be sent.
3. **Back up** before editing `automations.yaml`, `configuration.yaml` or files in `custom_components/`.
   Code changes need a **restart** of Home Assistant; a reload is not enough.
4. Do not delete BE WAVE entities to "clean up": helpers bound to them (e.g. `history_stats`) are deleted too.
   Rename instead.
5. Report honestly what you verified and what you could not (smoke, water, gate movement need a person).
6. Never print or store passwords and tokens in files that may be shared.

### Install

- **HACS:** HACS → ⋮ → Custom repositories → `https://github.com/matejrondzik/ha-satel-bewave`, category
  *Integration* → download → restart.
- **Manual:** copy `custom_components/bewave/` into `<config>/custom_components/`, restart.
- **Add:** Settings → Devices & services → Add integration → *BE WAVE (Satel)*. The integration scans the
  network (UDP 4111) and lists the hubs found; then the login and password of a Be Wave app user with rights
  to the devices (usually the administrator). No hub found = other VLAN / broadcast blocked → enter the IP.
- **Wired zones:** after setup *Settings → Repairs* asks what is connected to Z1, Z2, … (later: Configure →
  Wired zones). Ask the user - do not guess from names.
- Errors: *cannot connect* = TCP 4200 not reachable (IP, VLAN, firewall); *invalid auth* = wrong login or
  password; *device limit* = the user already has 5 mobile devices → remove an unused one in the Be Wave app.
- Home Assistant appears in the Be Wave app as one more mobile device of that user. It must not be deleted.

### What the integration creates

| Device | Entities |
|---|---|
| Hub | `alarm_control_panel` (arm away / disarm the protection mode from the options), tamper, trouble (attribute `problems`), switches: status LED, Grade 2, Satel cloud, select *Heating mode (whole house)* |
| Detector | motion / opening `binary_sensor`, temperature, battery, signal, voltage, temperature offset `number` |
| SLIM LUNA detector | as detector + `light` (light ring) + `number` light-on time |
| Flood detector | moisture `binary_sensor` |
| Thermostat head ART-210 | `climate` (presets manual / schedule / eco / boost; attributes `schedules`, `schedule_name`, `reference_sensor`, `valve`…), valve opening sensor, numbers day / night / eco / manual temperature, offset, boost time |
| Panel output O1, O2 … | `switch` (attribute `output_type`: `bi` or `mono` pulse) |
| Panel wired zone Z1, Z2 … | `binary_sensor` with the type chosen in the setup (smoke, door, garage door, window…) |
| Keypad | keypad lockout `binary_sensor` |
| Virtual IP device | `switch` |
| Every device | tamper and trouble `binary_sensor` |

Each entity has the attribute `room` (room in the Be Wave app) - use it to assign Home Assistant areas.
Entity ids are derived from device names in the user's language; rename them to stable, readable ids.

### Useful facts

- Commands take ~1.5 s each (the hub drops back-to-back commands); a scene with 6 heads takes ~10 s.
- Thermostat schedules contain only time windows; temperatures are the heads' day / night values. Changing
  the day temperature during an active window needs `climate.set_temperature` too to apply now.
  Services: `bewave.save_schedule`, `bewave.delete_schedule`, `bewave.assign_schedule`.
- A mono output gives a pulse by itself - an automation only turns it on.
- More background: [`FINDINGS.md`](FINDINGS.md).

### Automation recipes

Flood / smoke / tamper notification (replace the notify service and entities):

```yaml
alias: BE WAVE - safety alerts
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.kitchen_flood
      - binary_sensor.garage_smoke
      - binary_sensor.hub_tamper
    to: "on"
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: "⚠️ {{ trigger.to_state.name }}"
      message: "{{ trigger.to_state.name }} at {{ now().strftime('%H:%M') }}"
      data:
        push:
          interruption-level: critical
mode: queued
```

Boiler from valve openings with a 15-minute pause after switching off:

```yaml
alias: Boiler by thermostat heads
triggers:
  - trigger: time_pattern
    minutes: /5
  - trigger: state
    entity_id: switch.boiler
    to: "off"
    for: { minutes: 15 }
variables:
  valves: >-
    {{ ['sensor.radiator_living_room_valve_opening', 'sensor.radiator_bedroom_valve_opening']
       | map('states') | map('float', 0) | list }}
actions:
  - choose:
      - conditions:
          - "{{ valves | max >= 10 }}"
          - condition: state
            entity_id: switch.boiler
            state: "off"
            for: { minutes: 15 }
        sequence:
          - action: switch.turn_on
            target: { entity_id: switch.boiler }
      - conditions:
          - "{{ valves | max < 10 }}"
          - condition: state
            entity_id: switch.boiler
            state: "on"
        sequence:
          - action: switch.turn_off
            target: { entity_id: switch.boiler }
mode: single
```

Gate pulse with confirmation is best done in the dashboard (e.g. a button with `confirmation:`), not by an
automation.

### Dashboard

[`dashboard.yaml`](dashboard.yaml) is a ready view built from native cards (alarm, motion, thermostat with
presets, temperatures, virtual devices, hub settings). Replace the entity ids with the user's.
