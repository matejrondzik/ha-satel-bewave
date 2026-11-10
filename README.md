<img src="custom_components/bewave/brand/logo.png" alt="BE WAVE" height="64">

# BE WAVE (Satel) for Home Assistant

[![hacs][hacs-badge]][hacs] [![Validate][validate-badge]][validate]

Local control of **Satel BE WAVE** hubs from Home Assistant. It works with the BE WAVE
Smart HUB and the HYBRID control panels (tested on **HYBRID 128 Plus**, firmware 1.05).
There is no cloud account and no extra bridge: Home Assistant talks to the hub on your LAN
using the same encrypted protocol as the Be Wave mobile app.

> **Credits.** This integration is based on
> **[slaveitgr/Satel_BeWAVE_Home_Assistant-](https://github.com/slaveitgr/Satel_BeWAVE_Home_Assistant-)**
> by **@slaveitgr**, who reverse-engineered the sign-in and arm/disarm protocol and published
> the first integration (MIT). This fork adds HYBRID panel support, two-way device control,
> thermostat heads and a reworked setup flow. See [what this fork adds](#what-this-fork-adds).

---

## Features

| Platform | What you get |
|---|---|
| `alarm_control_panel` | Arm away / disarm the chosen protection mode, live state pushed from the hub |
| `binary_sensor` | Detectors (PIR, door/window, BUS detectors); **tamper** and **trouble** of the hub and of every device, with readable trouble descriptions (e.g. *Tamper (cover open)*, *Valve out of range*, *Low battery*) |
| `sensor` | Temperature and battery of wireless devices, **valve opening** of thermostat heads |
| `climate` | **ART-210 thermostat heads**: target temperature, current temperature, valve position, presets *manual / schedule / eco / boost* |
| `select` | **Heating mode (whole house)**: one control that switches every thermostat head (manual / schedule / eco / boost), including heads added later. Shows *Mixed* when the heads differ. |
| `switch` | **Virtual IP devices** created in the Be Wave app, two-way; hub settings (status LED, Grade 2, Satel cloud) |

State changes are **pushed** by the hub (~1 s), so automations react immediately. Typical uses:

- *Leaving home*: arm the alarm in the Be Wave app or on the keypad → a virtual device turns
  on → Home Assistant closes the blinds and switches off the lights.
- *From Home Assistant*: switch a virtual device that a Be Wave scenario reacts to.
- *Heating*: set the temperature or preset of thermostat heads from Home Assistant.

## Requirements

- A BE WAVE Smart HUB or HYBRID control panel reachable from Home Assistant on **TCP port 4200**
  (same LAN or routed VLAN). A fixed IP address for the hub is recommended.
- A user account on the hub (the same login you use in the Be Wave app). To control devices,
  use a user with rights to them, typically the administrator.
- One free **mobile-device slot** for that user. Home Assistant registers as one more phone.
- Home Assistant **2025.1** or newer.

## Installation

### HACS (recommended)
1. HACS → ⋮ → **Custom repositories** → add `https://github.com/matejrondzik/ha-satel-bewave`,
   category **Integration**.
2. Search for **BE WAVE (Satel)** → **Download**.
3. Restart Home Assistant.

### Manual
Copy `custom_components/bewave/` into `config/custom_components/` and restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → BE WAVE (Satel).** If the hub is on the
same network, Home Assistant usually discovers it by itself (Satel MAC prefix) and offers it
under *Discovered*.

1. **Hub IP address.** Prefilled when discovered.
2. **Login and password.** The same ones you use in the Be Wave app.
3. Done. In the Be Wave app the user now has one more mobile device. That is Home Assistant;
   do not delete it.

Each problem gets a clear message: unreachable hub, wrong login or password, or *no free
mobile-device slot* (remove an unused phone in the Be Wave app).

### Later changes
| Situation | Where |
|---|---|
| Password changed in the Be Wave app | Home Assistant shows **Re-authenticate**. Enter the new password; no new device slot is used. |
| Hub got a new IP address | Integration ⋮ → **Reconfigure** |
| Arm / disarm a different protection mode | Integration → **Configure** → *Protection mode* (`defau` = default, whole object) |
| Reporting a bug | Integration ⋮ → **Download diagnostics**. Credentials, serial number and device names are removed automatically. |

## Dashboard

[`docs/dashboard.yaml`](docs/dashboard.yaml) is a ready-made **Security** view built only from native
Home Assistant cards (no HACS cards needed):

- an alarm tile with *Arm away / Disarm* buttons and motion detectors per room,
- a thermostat with preset buttons (manual / schedule / eco / boost), valve opening and head battery,
- temperature tiles with 24 h trend graphs,
- virtual devices with toggles,
- hub settings, where switches that matter (Grade 2, SATEL cloud) ask for confirmation, and batteries.

Paste it via *Dashboard → Edit → Raw configuration editor* and replace the entity ids with yours.

## Good to know

- **Virtual IP devices** are the cleanest way to link Be Wave scenarios with Home Assistant.
  Create one in the Be Wave app (*Add device → Virtual IP device*); it appears here as a
  switch. Both sides see each other's changes.
- **Echoes:** when a Home Assistant automation and a Be Wave scenario react to each other, make
  the automations idempotent (only act when the state really differs) and add a short `for:`
  debounce. Otherwise two systems can toggle each other.
- **Thermostat heads:** *manual* sets a fixed temperature and *schedule* follows the Be Wave
  schedule. *Eco* uses the eco temperature. *Boost* opens the valve for the boost time set in
  the app.
- **Not supported (yet):** bypassing zones, editing scenarios, and blinds/dimmers modules
  (the protocol has the commands, but they are untested).
- The integration **never** sends firmware update, restart or factory-reset commands.

## What this fork adds

Compared with the original v0.4.4 by @slaveitgr:

- **HYBRID panel support:** user and device IDs are assigned by the hub, a 1 s keepalive
  stops the HYBRID from dropping idle sessions, push updates, UTF-8 names and BUS detector
  types.
- **Device control:** app-style registration with credentials, so the hub accepts
  `DeviceStateUpdate` commands. A token registered without credentials is read-only.
- **Switches** for virtual IP devices and **climate** entities for ART-210 thermostat heads
  (target temperature, presets, valve-based HVAC action).
- **Reliability:** an immediate reconnect before reporting a failure, plus re-authentication
  when the password changes.
- **Setup flow:** fewer fields with explanations, specific error messages, reauth,
  reconfigure, options with protection mode, diagnostics, and English and Slovak translations.
- **Protocol notes** in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Troubleshooting

| Message / symptom | Fix |
|---|---|
| *Cannot reach the hub* | Check the IP, firewall/VLAN rules for TCP 4200, and that the hub is powered. |
| *No free mobile-device slot* | Be Wave app → Users → *your user* → remove an old mobile device. |
| Switches do nothing, states still update | The user has no rights to the device, or an old entry was registered read-only. Remove and add the integration again. |
| Entities briefly unavailable when the phone app is open | The hub limits parallel sessions. The integration reconnects by itself within seconds. |

Enable debug logging:
```yaml
logger:
  logs:
    custom_components.bewave: debug
```

## License and disclaimer

MIT. See [LICENSE](LICENSE). Original work © @slaveitgr and contributors; additions ©
Matej Rondzik.

This is an unofficial project, not affiliated with or endorsed by SATEL sp. z o.o. "Satel" and
"BE WAVE" and the BE WAVE logo are trademarks of their owners and are used only to identify the supported devices. The protocol was documented by observing the local
communication of devices the authors own. Use at your own risk, especially with security
functions.

[hacs]: https://hacs.xyz
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate]: https://github.com/matejrondzik/ha-satel-bewave/actions/workflows/validate.yml
[validate-badge]: https://github.com/matejrondzik/ha-satel-bewave/actions/workflows/validate.yml/badge.svg
