# AGENTS.md

Instructions for AI coding agents are in [`CLAUDE.md`](CLAUDE.md) - read it before changing anything.

Short version:

- This integration controls an **alarm system**. Never send `SystemTrigger` (restart / factory reset /
  firmware update) and never arm, disarm, switch outputs or change heating on a real system without the
  user's explicit OK for that action.
- One patch per message, ≥ 1.5 s apart, verify the result (`hub.set_patch`).
- Never change existing entity `unique_id`s. English first; keep `strings.json` = `translations/en.json`
  and update `translations/sk.json`.
- Setting up the integration for a user: [`docs/AI_SETUP.md`](docs/AI_SETUP.md).
  New device types: [`docs/ADDING_DEVICES.md`](docs/ADDING_DEVICES.md).
- Tests: `pip install pytest cryptography && pytest -q tests`.
