# AGENTS.md

Instructions for AI coding agents and contributors working on this repository.

## Project

`tti1908dmm` is a Python toolkit for the Aim-TTi 1908 / 1908P bench multimeter.

The repository has two main jobs:

- Provide a small synchronous Python driver for the meter's SCPI-like USB serial interface.
- Provide a native HID firmware flasher for the meter bootloader, replacing the Windows-only vendor tool for this workflow.

This project is independent reverse-engineering work. Be careful with claims about vendor support or compatibility.

## Read first

Before changing code, read the relevant entry points:

- `README.md` for user-facing setup, safety notes, and workflow.
- `tti1908.py` for the driver API and serial protocol assumptions.
- `firmware/flash.py` before touching anything related to firmware or HID transport.
- `tests/` to understand the expected behavior that can be verified without hardware.

## Code map

| Path | Purpose |
| --- | --- |
| `tti1908.py` | Main driver, `Reading` dataclass, response parser, serial command wrapper |
| `comms_check.py` | Minimal hardware canary; keep this small |
| `demo.py` | Broader hardware smoke test for verified SCPI commands |
| `firmware/flash.py` | TTi HEX parser and HID bootloader flasher |
| `tests/` | Unit tests for parsing, mocked serial I/O, and firmware parsing |

## Development commands

Use the repository root as the working directory.

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m pytest
```

For hardware checks:

```sh
venv/bin/python comms_check.py
venv/bin/python demo.py
```

For firmware parser/flasher checks:

```sh
venv/bin/python firmware/flash.py path/to/firmware.hex --dry-run
```

## Hardware safety

Treat real hardware access as potentially destructive.

- Do not run the real flasher unless the user explicitly asks for it.
- Always run `firmware/flash.py --dry-run` before any real flash attempt.
- Do not modify vendor firmware files.
- Do not send undocumented recovery bytes to the meter. If the device is in a strange state, stop and ask the user to power-cycle or re-enter bootloader mode.
- Failed flashes may be recoverable through the ROM bootloader, but they are still serious events.

Normal USB serial mode is `103e:04c4`.

Bootloader mode is `103e:042e`.

## USB and serial assumptions

The driver auto-detects ports in this order:

- `/dev/serial/by-id/usb-TTI_1908_DMM_64638987-if00`
- `/dev/tty.usbmodem*`
- `/dev/ttyACM*`

Do not hardcode a transient `/dev/ttyACM1`-style path in examples or tests unless the user specifically provides one.

Some 1908 units fail enumeration when plugged directly into strict xHCI/USB 3.x ports because their USB descriptor transfer is truncated. A physical USB 2.0 hub can work around this. This happens before Python code runs, so do not try to "fix" it in the driver.

## Driver guidance

- Keep the driver small and synchronous.
- Preserve the existing `TTI1908` context-manager interface.
- `query()` intentionally drains stale bytes and skips non-ASCII garbage; do not remove that behavior without a hardware-backed reason.
- `parse_reading()` must continue to handle normal numeric readings, `OVLOAD`, and `OVFLOW`.
- Keep `comms_check.py` as a minimal "does it talk?" script. Put broader experiments in `demo.py`.

## Firmware flasher guidance

The flasher protocol is timing- and byte-sensitive.

- Understand `ProcessorData`, `parse_hex()`, `build_initial_report()`, `build_data_report()`, `read_ack()`, and `flash_one_processor()` before editing the flashing path.
- Linux `hidapi.write()` expects a leading `0x00` byte even though the device descriptor does not define a report ID. Keep this quirk unless tested otherwise.
- The bootloader acknowledgement counter can produce stale reads; the code drains to the newest counter value on purpose.
- The counter resets to `0` after the final data block. Do not simplify that away.

## Testing expectations

- Run `venv/bin/python -m pytest` after code changes.
- Add or update tests when changing parsing logic, serial query behavior, or HEX handling.
- Hardware-only behavior should be documented clearly when it cannot be covered by unit tests.

## Documentation expectations

- Keep `README.md` practical and user-facing.
- Keep warnings around USB topology and firmware flashing visible.
- If behavior changes in code, update the README and examples in the same change.
- Use clear English in repository documentation.

## Style

- Follow the existing straightforward Python style.
- Prefer standard-library code unless a dependency is already present and useful.
- Keep comments focused on protocol details, hardware quirks, and safety-critical behavior.
- Avoid broad refactors unless they are necessary for the requested change.
