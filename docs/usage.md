# Usage

Requires Python 3.12 or newer. Install the library with `pip install ac-infinity-ble`
or install this development checkout with `pip install -e .`.

## Read a controller

The standalone example uses current Bleak discovery APIs, a bounded scan, and
cleanup on success, failure, or cancellation:

```shell
python examples/run.py --address YOUR_BLUETOOTH_ADDRESS --timeout 30
```

Omit `--address` to select the first supported advertisement. On macOS, use the
Bluetooth UUID instead of a MAC address. The example reads state only.

If you already have a Bleak device and its advertisement:

```python
from ac_infinity_ble import ACInfinityController

controller = ACInfinityController(ble_device, advertisement_data=advertisement)
try:
    await controller.update()  # Saved root mode and ON/OFF settings
    await controller.refresh_telemetry()  # Actual output and connected loads
    print(controller.state.outputs)
finally:
    await controller.stop()
```

`update(port=1)` reads saved settings for one addressed port. Advertisement-only
sensors do not open a connection for either operation. Temperature is Celsius,
humidity is percent, and VPD is kPa; missing readings are `None`.

## Control an output

After discovering a connected output, use its ID from `controller.state.outputs`:

```python
await controller.set_output(port.id, on=True, level=5)
await controller.set_output(port.id, on=False)
```

Levels range from 0 to 10. The OFF operation selects the device's OFF mode and
preserves its configured minimum; OFF does not necessarily mean zero physical
movement. Home appliances instead use their separate power flag. Controller
`is_on` describes its operating mode; `speed` reports actual output independently.

Newer controller protocols pack manual speed and maximum speed together. The
library reads the current parameter before changing manual speed and preserves
the maximum. Mode-only commands preserve both saved levels.

Register callbacks using `remove = controller.register_callback(callback)` and
call `remove()` when finished. Callbacks receive an immutable `DeviceInfo` and a
`CallbackType`. Updates replace snapshots; neither a callback nor the original
input port dictionary can mutate controller state.

Protocol errors derive from `ProtocolError` and `ValueError`. Frame mismatches,
malformed parameters, and negative acknowledgements have distinct subclasses.
Transport errors remain Bleak errors; task cancellation propagates. Always await
`stop()` to finish notification cleanup and disconnect. Otherwise an active
connection is released after five idle seconds.

## Home Assistant polling

The integration keeps Home Assistant's Bluetooth coordinator as the scheduler:

- Advertisements refresh climate readings without connecting.
- Actual port telemetry is refreshed after 30 seconds without a notification.
- Saved settings are read at startup, for newly connected/replaced loads, and
  every five minutes for the root and known outputs.
- Notification-only refreshes subscribe and wait up to five seconds for a fresh
  frame. They do not write commands or repeatedly read settings.
- Background failures back off by 30, 60, 120, 240, then 300 seconds. User commands
  bypass this background scheduling delay.
- Polling only occurs when HA is running and has a connectable Bluetooth route.
  Unload cancels an active poll before stopping the controller.

These are freshness targets, not fixed timers: advertisements trigger HA's polling
scheduler, so a missing advertisement or connection route can delay a refresh.
