# How the hub behaves - lessons learned

Things found while building this integration against a **HYBRID 128 Plus (firmware 1.05)** with real
devices. They explain most "why does it work like this" questions. Protocol details are in
[`PROTOCOL.md`](PROTOCOL.md).

## Sessions and registration

- **Register like the app.** A device token signed in only with the token (as the original integration did)
  is silently **read-only for devices**: arming and hub settings work, `DeviceStateUpdate` is ignored.
  Signing in with login + password + token + hub identifier gives full control.
- **5 mobile devices per user.** Every new token takes a slot; the sixth sign-in fails with header error
  **31**. Remove old phones / test tokens in the Be Wave app (Users → user → mobile devices). Reauth and
  reconfigure in this integration reuse the stored token, so they never use a new slot.
- **One session per token.** A second connection with the same token as the running integration can
  arm / switch virtual devices, but commands to **physical** devices are ignored. Testing tools need their
  own token.
- The panel allows several local sessions at once (HA, the app, tools). Opening the app can drop an older
  session; the integration reconnects within seconds.
- **Idle timeout ~2.7 s** on the HYBRID - a keepalive every 1 s keeps the session and gives push updates.
- The HYBRID assigns user / device ids in the sign-in response header; use them, not fixed values.

## Sending commands

- **Back-to-back commands are dropped.** A second patch within ~1 s of the previous one is often lost, with
  no error. Keep ≥ 1.5 s between commands and verify each change in the state.
- **Several patches in one `DeviceStateUpdate` → the hub closes the session** (the app does this, the
  local session may not). Send one patch per message.
- The response to `DeviceStateUpdate` is empty even on success - only the pushed state tells the truth.
- **Do not re-subscribe** (`StructureGet` 44, schedules 46) in a live session - the hub disconnects.
- `StructureUpdateDevice` (104, device configuration such as output type or pulse length) is rejected on
  the integration's session - change configuration in the app.
- After a burst of schedule writes the hub once dropped off the network for a few minutes (cause not
  confirmed). Write schedules rarely, not in loops.

## Device keys

- Wireless devices have a unique uuid. **Main-board I/O** (outputs O1, O2, …; wired zones Z1, Z2, …) shares
  uuid **1** and is told apart by key field 2 (Z1 = 1, Z2 = 2, O1 = 5, O2 = 6). A patch with only the uuid
  is ignored or drops the session; the integration uses composite ids `1001, 1002, 1005, 1006`.
- Removing an entity in Home Assistant also removes helpers that point at it (e.g. `history_stats`). When a
  device id changes, recreate such helpers with the same entity ids.

## Outputs and zones

- Outputs are **bi** (on until off) or **mono** (a pulse timed by the hub, e.g. a gate opener), set in the
  app (`outputReactionType`). For mono, turning the switch on is enough.
- Wired zones report only violated / not violated (state field 9). Their meaning (smoke, door, gate magnet)
  is not in the protocol - set the device class in Home Assistant. With an NC contact, *violated* = open.

## Thermostat heads (ART-210)

- Modes: 0 manual, 1 schedule, 2 eco; boost is separate (107). Changing the mode makes the head take the
  temperature of that mode as its target (61).
- Temperatures accept **0.1 °C** steps.
- **Schedules have no temperatures** - only weekly time windows. Inside a window a head in schedule mode heats
  to its *day* temperature, outside to the *night* one. Schedules are hub-wide and shared by heads.
- In schedule mode the head recalculates its target only when the mode is switched on or at a window edge.
  Changing the day temperature in the middle of a window has no effect until then - set the target
  (`climate.set_temperature`) as well.
- Assigning a schedule (patch 50) **switches the head back to manual** - send patch 60 = 1 a few seconds later.
- **Reference sensor:** a head can regulate by another detector's temperature (set in the app). Field 67 is
  32-bit; BUS detectors (LUNA) have 64-bit ids and only fit in field 118. Setting a LUNA detector through 67
  stores a wrong id. The climate entity shows the reference sensor's temperature as the room temperature.
- A head without a reference sensor measures at the radiator and tends to close too early.
- Wireless heads wake up periodically - their state can lag a few minutes behind a command.
- A valve at 100 % for long can raise trouble *valve out of range*.

## Detectors

- APD-200 / APMD-250 report temperature and accept a **temperature offset** (66). LUNA BUS detectors do not
  list 66 in their commands but carry the field in their state.
- **SLIM LUNA**: the light ring is on/off (1) with a light-on time after motion (21, 0-180 s, *optical
  signalling period* in the app). They do **not** report ambient light level.
- AFD-200 flood detectors use a generic telemetry type; water = state field 9.

## Keypads

- Keypad lockout after wrong codes is state field 112. Event history is available (`SystemEvent` 32) with
  readable messages such as "Armed by user - admin".
- NFC cards and keypad codes have their own messages - not edited by this integration on purpose.

## Hub

- Troubles come as code lists: hub (SystemInfo status block field 12, memory 27) and devices (state field 1 of
  `HubDeviceState`). `errors.py` maps them to readable text.
- No MQTT. The cloud connection, status LED and Grade 2 are toggles (`SystemTrigger` 11 / 5 / 12).
- **Discovery:** the hub answers an unencrypted ping on UDP 4111 (broadcast) with its serial, name, address
  and firmware - this is how the app finds it, and how the integration finds hubs and follows IP changes.
  Broadcasts do not cross VLANs; then enter the IP. DHCP discovery (Satel MAC prefix `00:1B:9C`) is a fallback.
