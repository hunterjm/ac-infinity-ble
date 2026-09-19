# Changelog


## Unreleased

- Require Python 3.12+, Bleak 3.x and retry-connector 4.x; test Python 3.12–3.14.
- Validate command length, CRCs, command and sequence independently of telemetry.
- Route APK-verified device families and support independent connected outputs.
- Read actual Bluetooth revisions and expose immutable state snapshots, named
  controller modes and specific protocol errors.
- Preserve saved limits, including packed manual/maximum levels on newer protocols.
- Invalidate saved settings and reject pending acknowledgements when a port's
  connected load identity changes.
- Serialize connection cleanup, recover supported service caches, and add bounded
  notification-only refreshes with deterministic shutdown.
- Replace Python formatting/lint tools with Ruff, modernize CI and release
  automation, document the protocol in Markdown, and repair the standalone example.
- The companion HA integration adds device-specific controls, schema 1.3 migration,
  30-second port freshness, five-minute settings reads and background backoff.

<!--next-version-placeholder-->

## v0.4.3 (2023-09-15)

### Fix

* Bleak has looser version requirements ([#8](https://github.com/hunterjm/ac-infinity-ble/issues/8)) ([`192768e`](https://github.com/hunterjm/ac-infinity-ble/commit/192768e0a43701bb111496c71e72849f56ace3c0))

## v0.4.2 (2023-07-15)

### Fix

* Only send one command at a time and wait for response ([#5](https://github.com/hunterjm/ac-infinity-ble/issues/5)) ([`495f44e`](https://github.com/hunterjm/ac-infinity-ble/commit/495f44e0f51a373e52d0148255d96a3e68efb908))

## v0.4.1 (2023-07-14)

### Fix

* Do not replace None values in state on new adv data ([`96a124c`](https://github.com/hunterjm/ac-infinity-ble/commit/96a124c2c672a9a834a041aff97b3d2edb85de08))

## v0.4.0 (2023-07-14)

### Feature

* Fix set level for non 69 pro fans ([#4](https://github.com/hunterjm/ac-infinity-ble/issues/4)) ([`3d1fe16`](https://github.com/hunterjm/ac-infinity-ble/commit/3d1fe1631d53b9e337075d9d21ccb60df0cacd78))

## v0.3.0 (2023-07-14)

### Feature

* Only fire callback on update ([#3](https://github.com/hunterjm/ac-infinity-ble/issues/3)) ([`a1f90ca`](https://github.com/hunterjm/ac-infinity-ble/commit/a1f90ca290a5492db780fe79b28b6ff9783ac8fa))

## v0.2.0 (2023-07-14)

### Feature

* Update ble device and adv data ([#2](https://github.com/hunterjm/ac-infinity-ble/issues/2)) ([`6c46276`](https://github.com/hunterjm/ac-infinity-ble/commit/6c4627638194b5d5a593120864c7ea760df3c510))

## v0.1.0 (2023-07-14)

### Feature

* Initial release ([#1](https://github.com/hunterjm/ac-infinity-ble/issues/1)) ([`8ff9789`](https://github.com/hunterjm/ac-infinity-ble/commit/8ff978970911a96e1d139ff5dd45960d7201a203))
