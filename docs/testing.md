# Testing

## Library checks

Use Python 3.12 or newer and Poetry 2.4.3:

```shell
poetry install --with docs
poetry run pytest --cov=ac_infinity_ble
poetry run mypy src tests examples
poetry run ruff check .
poetry run ruff format --check .
poetry run sphinx-build -W --keep-going -b html docs docs/_build/html
poetry check --lock
poetry build
pre-commit run --all-files
```

CI runs the library on Python 3.12, 3.13 and 3.14 on Linux and Windows. It also
installs the built wheel into a fresh environment and imports it outside the
source checkout, checking packaging independently of editable installs.

Protocol fixtures cover family/version boundaries, frame length and CRC checks,
command/sequence matching, fragmented replies, interleaved unsolicited telemetry,
port acknowledgements and packed settings. Captured Controller 69 Pro frames in
`tests/test_hardware_frames.py` are separate from synthetic APK-layout fixtures.
Transport tests mock Bleak to exercise retries, timeouts, cancellation, service
cache recovery, adapter changes and cleanup without requiring hardware.

## Home Assistant checks

The integration repository provides two suites. `tests/` uses lightweight HA API
doubles for fast unit tests. `tests_ha/` uses real Home Assistant 2026.9.3 config
entries, device registry and coordinator classes, with mocked Bluetooth and
platform forwarding. It covers migration, setup, stable device identity through
unload/reload, active-poll cancellation, route selection and platform imports.

Install the library wheel and run from the integration repository:

```shell
python -m pip install -r requirements-test.txt
python -m pytest tests
```

For the real HA API suite, use Linux and Python 3.14:

```shell
python -m pip install -r requirements-ha-test.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests_ha
```

Run the suites in separate processes. Neither suite validates a physical local
adapter or Bluetooth proxy. `requirements-ha-test.txt` pins HA and the dependencies
from its Bluetooth component manifests; update them together when changing the
supported HA baseline.

## Hardware validation

For each model, firmware and adapter/proxy combination:

1. Verify discovery, climate readings, connected port IDs, load kinds and optional
   software/hardware revisions against the device and app.
2. Read both controller-wide and per-port settings. Record modes and ON/OFF presets
   before making a reversible change; do not assume the saved level equals actual
   output.
3. Change one connected output at a time and verify its ACK, GET readback and
   telemetry. Confirm other outputs are unaffected. Restore all recorded settings
   and verify the final physical output.
4. Check OFF mode with the configured minimum, including a nonzero minimum where
   appropriate. Check that changing manual speed preserves the maximum on packed
   protocols.
5. Exercise notification-only refresh, idle disconnect, reconnect, integration
   unload/reload and HA shutdown. Verify the phone app can connect afterward.
6. Interrupt and restore the adapter/proxy during an operation. Confirm cleanup,
   fresh route selection, rejection of stale replies and recovery. Include
   prolonged operation and multiple connected loads where supported.

Capture only the protocol data needed for regression fixtures; exclude addresses
and advertised identities. Distinguish captured frames from generated fixtures.
APK-derived support does not establish hardware compatibility by itself; see the
[compatibility evidence](protocol-validation.md#compatibility-evidence).
