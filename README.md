# Bentel Absoluta for Home Assistant

A Home Assistant custom integration for the **Bentel Absoluta** alarm
system.

This is an **unofficial integration**. It is not
affiliated with, endorsed by, or supported by Bentel.

## Features

- **Alarm control**: a global arm/disarm control for the whole panel, plus
  one full 4-mode control per partition (Disarm / Stay / Away / No Delay).
- **Zones**: open/closed state and alarm-triggered state per zone, plus a
  bypass switch for zones that support it.
- **Insertion-mode groups**: the panel's own configurable arming groups,
  exposed as switches.
- **Diagnostics**: installer remote-access status, a top-level
  alarm-active flag, a per-partition "not ready to arm" flag, and a raw
  warning count.

## How it connects

Same as the mobile app: a panel **serial number** and **PIN** — there's no
separate account/login. The integration polls a session-independent
notification endpoint for near-real-time updates, and only opens a real
session on the panel briefly when you actually issue a command or when a
periodic full-status refresh is due — never held open continuously, so it
won't lock the mobile app out of the panel.

## Installation

### HACS (custom repository)

Until this integration has its own dedicated repository, add it in HACS
as a custom repository pointing at this repo, with `ha-bentel-absoluta`
as the integration's directory.

### Manual

Copy `custom_components/bentel_absoluta/` into your Home Assistant
config's `custom_components/` directory, then restart Home Assistant.

## Configuration

Configuration is done entirely through the UI (Settings → Devices &
Services → Add Integration → Bentel Absoluta):

- **Name** — whatever you want to call this panel in Home Assistant.
- **Panel serial**
- **PIN**

No YAML configuration is supported or needed.

## Known limitations (v1)

- **Tamper, low-battery, and mains-loss conditions are not yet exposed**
  as sensors — the underlying fields for these aren't confirmed against a
  real occurrence yet. Alarm-triggered *is* fully supported. Planned for a
  future release once confirmed.
- **"No Delay" arming is mapped onto Home Assistant's "Night" arm
  mode.** This is a naming compromise, not a semantic claim — Bentel's
  "No Delay" mode actually means "Away, but arm immediately instead of
  waiting out the configured exit delay," not anything night-specific.
  Home Assistant's stock alarm card will label this button "Night"
  regardless.
- Output/PGM control (scenarios) is out of scope for v1.

## Support / issues

This integration talks to a private, undocumented cloud API. Behavior can vary by panel model, firmware,
and configuration in ways this project hasn't seen yet. Please open an
issue with as much detail as possible (and never include your real panel
serial, PIN, or session tokens).
