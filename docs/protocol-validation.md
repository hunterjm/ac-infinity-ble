# Bluetooth protocol

The protocol source of truth is the AC Infinity Android `com.eternal.acinfinity`
APK, version 2.0.8 (version code 123). The descriptions below are derived from
its packet builders, parsers, model dispatch, and Bluetooth transport code.
The XAPK SHA-256 is
`04d937bab2af0e796af42be5e36e065c7ee8914310870590aa4899b86270edca`.
The proprietary APK and decompiled sources are not distributed with this library.

## Frame validation

APK `BaseProtocol` (`zt`) builds the following layout, which the APK's
`StateMachine` (`u47`) uses to assemble received command packets:

| Bytes    | Meaning                                                          |
| -------- | ---------------------------------------------------------------- |
| 0–1      | Outgoing `a5 00`; accepted command header family `a5 00`–`a5 1f` |
| 2–3      | Big-endian payload length; complete packet is this length + 12   |
| 4–5      | Unsigned big-endian sequence                                     |
| 6–7      | Header CRC over bytes 0–5                                        |
| 8–9      | Command; the commands used here have byte 8 = 0                  |
| 10…      | Payload                                                          |
| Last two | Body CRC over bytes 8 through the end of the payload             |

Both CRCs use CRC-16/CCITT with initial value `0xffff`, stored big-endian.
The APK implements the checksum in `CRCUtil` (`m30`). Regression fixtures use
independent `binascii.crc_hqx` checksums and captured response bytes.

The library follows the APK packet assembler's `a5 00`–`a5 1f` header family.
Captured protocol-3 replies use `a5 13`; a hardware-tested Controller 69 Pro with
protocol revision 7 and software 3.2.56 uses `a5 17`. Header recognition still requires
exact length, both CRCs, command, sequence, or command-payload validation.

The APK assembler (`u47`) checks the header family and declared length;
`BleServer` (`iy`) matches the pending sequence. Its outgoing packet builders
calculate both CRCs. The examined incoming path does not establish equivalent
CRC or expected-command checks. This library deliberately adds those stricter
reply acceptance checks; they are not attributed to the APK receiver.

Command replies must match exact framing, both CRCs, command, and sequence.
The controller registers the future before writing, assembles fragmented
responses, and clears pending state on success, failure, timeout, and cancellation.
Malformed, stale, duplicate, or unrelated responses cannot complete that future.

`1e ff 02 09` notifications have their own framing and assembly buffer. They
continue publishing telemetry while a command is pending, including between
response fragments. These notifications do not have the command frame's CRC
fields. Their declared length and family-specific record boundaries are checked.

GET payloads are parsed as TLVs, rather than at fixed offsets. SET acknowledgements
must name every written parameter, report success, and echo the requested port on
multiport devices. Local command state is published only after this validation.
OFF changes mode without overwriting the saved ON or OFF levels.
GET mode parameters describe saved settings. Actual output comes from telemetry
and may differ from the saved level; a settings read preserves that measurement.

### Parameters and command payloads

For the operations implemented here, `0x01` reads parameters and `0x03` writes
them. GET requests contain a list of parameter IDs. GET replies and SET requests
contain `ID, length, value` records. The length is one byte except for H4, where
it is two bytes, big-endian. Multiport messages end with `ff, port`; that trailer
is not a TLV. The shared non-multiport path omits it.

| Parameter    | Controller meaning                                                        | Home-appliance meaning                   |
| ------------ | ------------------------------------------------------------------------- | ---------------------------------------- |
| `10`         | Mode (`01` OFF, `02` ON; other values select automatic modes)             | Work mode; `01` is manual, not OFF       |
| `11`         | Saved OFF output level                                                    | Direction; not an OFF level              |
| `12`         | ON level: low nibble through v7; packed manual/maximum on newer protocols | Output level                             |
| `13`         | Automatic temperature/humidity settings; version-specific layout          | Temperature trigger settings             |
| `16`         | Cycle settings                                                            | Dedicated power flag (`00` off, `01` on) |
| `ff` trailer | Port selection                                                            | Port selection                           |

These differences are why the library does not send a generic controller OFF
packet to a home appliance. For example, a Controller 69 Pro mode-only OFF
payload for port 1 is `10 01 01 ff 01`; H4 encodes the same operation as
`10 00 01 01 ff 01`. Home-appliance power-off on its root port is
`16 01 00 ff 00`. All three are enclosed in a command frame with command `03`.

The APK's `BaseProtocol.checkResult` reads parameter/result pairs and treats
result `01` as failure. The library requires each requested parameter's explicit
`00` success result, plus the correct port trailer, and rejects unknown result
codes. This stronger acceptance rule prevents partial or ambiguous ACKs from
publishing local success.

### Telemetry and advertisements

| Format                                                | Length                     | Layout after framing                                                                                                         |
| ----------------------------------------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Legacy controller                                     | `6 + byte[5]`              | Flags at 6; selected port at 7; temperature/RH/VPD at 8/10/12; root output at 14–17; four-byte physical-port records from 18 |
| Controller v6 layout (selected by APK for version ≥5) | `6 + byte[5]`              | Same climate readings; root level in bits 2–5 of byte 16; eight-byte physical-port records from 22                           |
| C-family sensors                                      | Fixed 31                   | Identity/version at 15/16; climate readings from 19; v4 uses packed signed-magnitude 12-bit values                           |
| H                                                     | `6 + byte[5]`              | Counted calibration records, ten-byte port records, then four-byte sensor records                                            |
| H4                                                    | `7 + uint16_be[5:7]`       | Explicit port IDs in eleven-byte records; sensor ordering block; five-byte sensor records                                    |
| Home appliances                                       | `6 + byte[5]`, at least 28 | Level in high nibble of 7; inside/outside temperature at 12/14; power measurement at 16; work mode at 23                     |

Legacy/v6 climate measurements are signed 16-bit values in hundredths; `8000`
means missing. The home appliance's power _measurement_ is not its power-switch
state. AI sensor records specify their own accuracy: multiply by 10, 1, 0.1,
or 0.01 for accuracy codes 0–3. Primary climate sensor IDs are 0/1 (F/C),
2 (humidity), and 3 (VPD).

Advertisements use manufacturer ID 2306 (`0x0902`). The APK accepts 27-byte and
17-byte payloads. Bytes 0–5 identify the device; 6–10 normally contain its ASCII
name suffix; 11 is protocol version and 12 is device type. The extended payload
has climate flags at 13 and readings from 14. VPD Cloudcom sensors use bytes
6–10 for VPD data and derive the name from identity instead. Their packed
climate values begin at 15; other C v4 devices begin at 14. The library accepts
short advertisements for identity only, because extended readings cannot be
decoded safely from that length.

## Device Information revisions

The APK `BleServer` (`iy.f`) reads ASCII revision strings from GATT
characteristics, independently of the proprietary command channel:

| Standard characteristic  | APK `BleStatue` (`jy`) field | Meaning                                                                                                        |
| ------------------------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `2A28` Software Revision | `i`                          | Software revision; the app's Device Information screen displays this as its current version, prefixed with `F` |
| `2A27` Hardware Revision | `j`                          | Hardware revision                                                                                              |
| `2A26` Firmware Revision | `k`                          | Separate firmware revision when the characteristic is present                                                  |

These UUIDs use the Bluetooth base suffix `-0000-1000-8000-00805f9b34fb`.
`BleController` (`px.d4` and its hardware-read path) requests `2A28`/`2A27`;
`DeviceInformationModel.init` prefers the connected device's `i` value for the
displayed version. `DeviceRepository` (`eg1`) preserves the three distinct values.
The `2A26` handler does not prove that every controller exposes that characteristic.

The library optionally reads characteristics actually present in discovered
services and retains all three values separately. HA prefers `2A28` for its
software/firmware display to match the app, falling back to a reported `2A26`.
The advertisement's byte 11 remains a **protocol revision**, not a firmware
string. Empty, non-ASCII, or malformed strings remain unknown; the app's default
version substitutions are not used. These are read operations, not firmware
update or provisioning commands.

## APK protocol families

This is a code-derived support matrix, not a claim of hardware certification.
Names come from `DeviceConfig.json` and the app's family predicates. IDs for
controllers and IDs describing connected loads are different namespaces.

| Device IDs                              | APK path                                 | Implemented handling                                                                  |
| --------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------- |
| 1, 2, 6                                 | `ProtocolResolution` (`ro5`)             | Legacy reads, manual mode/level, telemetry                                            |
| 7, 8, 9, 11, 12, 16, 17, 18, 23         | `EFamilialResolution` (`tp1`)            | Port-addressed reads/writes; legacy or v6 telemetry                                   |
| 3, 4, 5, 14, 15, 24, 25, 34, 35         | Cloudcom / `CFamilialResolution` (`k30`) | Sensor advertisements and 31-byte telemetry; packed v4 sensor values; no fan entities |
| 19, 20, 21, 22, 26, 27                  | H family                                 | Counted port/sensor records; version-aware TLVs                                       |
| H family version ≥20; 51 at any version | `H4FamilialResolution` (`ml2`)           | Two-byte TLV lengths; extended telemetry length and explicit port IDs                 |
| 33, 39, 40, 48, 49, 50                  | `RoomToRoomFanResolution` (`jw5`)        | Dedicated power parameter `0x16`, level `0x12`, home telemetry                        |

Type 11 is Controller 69 Pro; type 18 is Controller 69 Pro+. Type 7 is the
Bluetooth Controller 69. The outlet variants include Controller 76, desktop and
wall controllers, and AI outlets. The C family includes Cloudcom A/B and VPD B
sensors. H contains the AI controllers. Home devices include through-wall fans,
Smart AC, Airtap AI, and circulation fans.

Catalog entries without verified protocol support are not enabled. A generic
dispatch fallback alone does not establish compatibility. IDs outside the
supported model list are rejected during discovery and command construction.

Catalog entries marked Wi-Fi-only (including type 8 and some newer home devices)
may expose Bluetooth only during setup or in a particular firmware mode. The
library has codecs for their APK Bluetooth dispatch paths; it cannot enable a
Bluetooth operational mode that the device does not expose. Camera IDs
10000–10002 use provisioning/OTA paths and are not treated as controllable UIS
devices.

The app resolves loads from a port type byte, a resistance range on older UIS
devices, or a child device ID. Decoders identify fans, lights, outlets,
humidifiers/dehumidifiers, heaters, and air conditioners using those same tables.
Only actually reported, connected ports get device-specific entities. Port 0's
historical aggregate fan unique ID is preserved where it existed.

The accompanying integration exposes brightness for lights, speed for fans,
and manual power plus native output levels for environmental appliances. These
are manual controls. Unsupported app features include
AI recipes, schedules, secondary oscillation functions, thermostat setpoints,
firmware updates, Wi-Fi provisioning, or cloud control. A home appliance's
power state remains unknown if the available telemetry does not report it;
the library does not confuse its manual mode with the UIS OFF mode.

## Connection lifecycle

Connect, subscribe, write, wait, and failure cleanup share one transaction
lock. All Bluetooth operations have deadlines. Writes are split into 20-byte
chunks, compatible with the minimum ATT MTU and the APK's split-write behavior.
Read and write characteristics must come from the same UUID pair.

The default idle disconnect is **5 seconds**. It groups rapid commands while
leaving a gap between routine HA polls. Notifications do not extend this timer.
Shutdown owns and awaits the timer's task, unsubscribes, and attempts disconnect
even if unsubscribe fails. HA supplies the currently connectable BLEDevice at
each new connection attempt, allowing adapter/proxy routing to change.

Retries reconnect before writing, and disconnect callbacks wake pending requests.
Partial connection/subscription failures, config-flow failures, integration
unload, and failed setup await cleanup. Backend disconnect failures are logged.

## Compatibility evidence

Controller 69 Pro (type 11, protocol revision 7, software 3.2.56) has hardware
coverage for discovery, root/port settings, manual fan speed, revision reads,
telemetry-only refresh, idle disconnect and reconnect using a local adapter.
Other families are implemented from the APK's dispatch paths and wire layouts;
that evidence does not establish compatibility with every firmware or physical
load combination. Physical proxy and multi-load validation is separate from
mocked transport tests. See [testing](testing.md) for the test suites and hardware
validation procedure.

## Units, modes, and packed manual levels

APK `com.eternal.base.protocol.a.getString4Degree/getTmp` divides wire temperature
by 100 and applies Celsius-to-Fahrenheit conversion only for display. The wire
reading is Celsius regardless of the unit-preference bit. `-32768` is missing;
nonzero temperature/humidity trend flags are not missing-reading indicators.

APK `tp1.initDeviceMinModel` and `setModelData` use the upper nibble of parameter
`0x12` as `onSelfSpead` after protocol revision 7. The lower nibble retains `typeOn`
(the maximum). `ml2` uses this packed layout for H4, including type 51 revision 1.
GET decodes manual ON level from the upper nibble; a manual-level SET first reads
the parameter, changes that nibble, and preserves the lower one. Older revisions
use the low nibble for manual ON level. Home-family power/level encoding is separate.

OFF mode (`1`) selects the saved minimum; ON mode (`2`) selects the saved manual
level (or the legacy ON level). Selecting OFF does not rewrite parameter `0x11` to zero. Actual movement comes from telemetry
and can be nonzero in OFF mode when a minimum is configured. The API exposes mode
and actual level independently.
