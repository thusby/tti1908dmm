# tti1908dmm

![TTi 1908 DMM on a slightly nerdy bench](nerd.png)

A small Python toolkit for the Aim-TTi 1908 / 1908P bench multimeter.

It contains:

- `tti1908.py`, a synchronous Python driver for the meter's SCPI-like USB command set.
- `comms_check.py`, a tiny communication canary for `*IDN?`, `MODE?`, and `READ?`.
- `demo.py`, a broader smoke test for the verified measurement modes.
- `firmware/flash.py`, a native HID firmware flasher based on the vendor bootloader protocol.

This project is independent reverse-engineering work. It is not affiliated with, endorsed by, or supported by Aim-TTi.

## Status

The serial driver covers the parts of the command set that have been tested against real hardware:

- identification and status queries
- main-display readings via `READ?`
- secondary-display readings via `READ2?`
- DC/AC voltage and current modes
- 2-wire and 4-wire resistance
- diode, continuity, capacitance, frequency, and temperature mode selection
- autorange/manual range switching
- hold, null, min/max, filter, and sampling-speed commands

The firmware flasher can parse TTi-flavoured Intel HEX files and speak the 1908 bootloader HID protocol. Flashing instrument firmware can permanently damage hardware if interrupted or used with the wrong file, so treat it as an expert tool.

## Requirements

- Python 3.12 or newer
- `pyserial`
- `hidapi` for firmware flashing
- Linux or macOS for the serial driver
- Linux for the bootloader/flasher workflow as currently documented
- Physical access to an Aim-TTi 1908 or 1908P

Install dependencies with:

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

On Linux, your user usually needs access to the serial device:

```sh
sudo usermod -aG dialout "$USER"
newgrp dialout
```

For bootloader flashing, you may also need a local `udev` rule for the HID device.

## USB connection notes

Normal operating mode appears as:

```text
103e:04c4
```

Bootloader mode appears as:

```text
103e:042e
```

On Linux, the driver first looks for the stable path:

```text
/dev/serial/by-id/usb-TTI_1908_DMM_64638987-if00
```

It then falls back to `/dev/tty.usbmodem*` on macOS and `/dev/ttyACM*` on Linux.

Some 1908 units have a USB descriptor problem when connected directly to strict xHCI/USB 3.x controllers. If Linux logs a message like this, put a physical USB 2.0 hub between the computer and the meter:

```text
config index 0 descriptor too short
cdc_acm ... failed with error -22
```

When this happens, the kernel fails before this Python code gets a chance to run.

## Quick check

With the meter connected and powered on:

```sh
venv/bin/python comms_check.py
```

Expected shape of output:

```text
IDN:   THURLBY THANDAR, 1908, , 1.09
MODE:  VDC, 100mV, AUTO
READ:  0.000000 V DC
```

Then run the fuller smoke test:

```sh
venv/bin/python demo.py
```

## Python usage

```python
from tti1908 import TTI1908

with TTI1908() as dmm:
    print(dmm.idn())

    dmm.vdc()
    reading = dmm.read()
    print(reading)

    dmm.ohms("1000")
    print(dmm.read())

    dmm.autorange()
    dmm.min_max_start()
    # wait while values change
    minimum, maximum = dmm.min_max()
    dmm.cancel_modifier()
```

`read()` returns a `Reading` dataclass:

```python
Reading(value=1.234, unit="V DC", raw="1.234 V DC")
```

`value` is `None` for overload or overflow replies.

## Firmware flashing

The firmware flasher is in `firmware/flash.py`. It replaces the Windows-only vendor flashing utility by speaking the HID bootloader protocol directly through `hidapi`.

Firmware images are not included in this repository. Download the correct 1908/1908P firmware from Aim-TTi and place the HEX file under `firmware/`.

Enter bootloader mode:

1. Power off the meter.
2. Hold the rear-panel `USB UPDATE` button.
3. Power on the meter.
4. Keep holding for about three seconds, then release.
5. Confirm that the bootloader is visible:

```sh
lsusb | grep 103e
# expected: 103e:042e
```

Always dry-run before flashing:

```sh
venv/bin/python firmware/flash.py firmware/1908_ALL_109_103.hex --dry-run
```

Flash only when the dry-run parses the file as expected:

```sh
venv/bin/python firmware/flash.py firmware/1908_ALL_109_103.hex
```

After reboot, verify communication again:

```sh
venv/bin/python comms_check.py
```

## Development

Run the unit tests with:

```sh
venv/bin/python -m pytest
```

The tests mock the serial device and exercise the parser, driver wrapper, and firmware HEX parser without requiring a connected meter.

Useful files:

| Path | Purpose |
| --- | --- |
| `tti1908.py` | Main driver API and reading parser |
| `comms_check.py` | Minimal communication check |
| `demo.py` | Hardware smoke test |
| `firmware/flash.py` | HID bootloader flasher |
| `tests/` | Unit tests that do not require hardware |

## Known limitations

- The `LIMITS` command has not behaved reliably on tested firmware.
- Firmware 1.07 could emit binary garbage after some filter/speed sequences; the driver drains stale non-ASCII replies defensively.
- The USB descriptor issue is a device/USB-topology problem, not a bug in this Python code.
- Flashing is inherently risky. Use the dry-run path and keep power and USB cabling stable.

## License

MIT. See `LICENSE`.
