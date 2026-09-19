# Installation

The library requires Python 3.12 or newer, Bleak 3.0.2+ (3.x), and
bleak-retry-connector 4.7.0+ (4.x). Python 3.12, 3.13 and 3.14 are tested.

## Published package

Install the latest published release from [PyPI](https://pypi.org/project/ac-infinity-ble/):

```shell
python -m pip install ac-infinity-ble
```

For development, install from the repository root:

```shell
python -m pip install -e .
```

To build a distributable wheel, install Poetry 2.4.3 and run `poetry build`. Install
the resulting wheel from `dist/` into the intended environment. Use an isolated
environment for development and testing.

## Home Assistant integration

The companion integration targets Home Assistant 2026.9+. Its library requirement
must match the release being tested. Publish the library before releasing the
integration so HACS can resolve that exact requirement. Development
HA tests use Python 3.14 on Linux and HA 2026.9.3's Bluetooth dependency versions.

Existing entries migrate automatically to schema 1.3. Generated names gain the
common model name; custom names and device/entity identities remain intact.
The advertised protocol revision is no longer shown as firmware. Actual optional
Bluetooth software/hardware revision strings populate the registry when available.
See [architecture](architecture.md#names-revisions-and-upgrades) for details.

## Upgrading library callers

State snapshots and their port maps are now immutable. Use `dataclasses.replace`
to construct a changed state instead of assigning fields. Saved ON/OFF presets
and actual output are separate: `update()` reads settings, while advertisements
and `refresh_telemetry()` update measurements. `is_on` describes operating mode;
actual output is available separately as `speed` or the port's `level`.

Protocol validation exceptions remain compatible with `ValueError`; transport
exceptions retain Bleak's types. Always await `controller.stop()` when finished.
See [usage](usage.md) for discovery, callbacks, multiport commands, and polling.
