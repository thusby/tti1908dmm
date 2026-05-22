"""
Minimal driver for the Aim-TTi 1908 / 1908P bench DMM over USB CDC-ACM.

Usage:
    from tti1908 import TTI1908

    with TTI1908() as dmm:
        print(dmm.idn())
        dmm.vdc()                  # switch main display to Vdc, autorange
        v, unit = dmm.read()
        print(v, unit)

Protocol notes (from the TTi 1908 manual, section 16):

* Commands are terminated with LF (0x0A).
* Responses are terminated with CR LF (0x0D 0x0A).
* Commands are sequential; a new command isn't parsed until the previous one
  completes. *OPC? always returns "1" immediately. No host-side pacing needed.
* Baud rate is ignored for USB operation.

Plug the DMM through a USB 2.0 hub, not directly into an xHCI port, or the
kernel fails to enumerate the bulk-IN endpoint. See README.md.
"""

from __future__ import annotations

import glob
import re
import time
from dataclasses import dataclass

import serial


def find_tti_serial_port():
    """Find the TTI 1908 serial port on any platform."""
    # Check for the standard Linux path first
    linux_path = "/dev/serial/by-id/usb-TTI_1908_DMM_64638987-if00"
    ports = glob.glob(linux_path)
    if ports:
        return ports[0]

    # Check for macOS USB serial paths
    mac_paths = glob.glob("/dev/tty.usbmodem*")
    if mac_paths:
        # Return the first matching path
        return mac_paths[0]

    # Check for generic ttyACM paths (fallback)
    acm_paths = glob.glob("/dev/ttyACM*")
    if acm_paths:
        return acm_paths[0]

    # If nothing found, return the default (will fail gracefully)
    return "/dev/serial/by-id/usb-TTI_1908_DMM_64638987-if00"


DEFAULT_PORT = find_tti_serial_port()


@dataclass
class Reading:
    """A single measurement from the DMM."""

    value: float | None  # None if overload/overflow
    unit: str  # e.g. "V DC", "Ohms", "Hz"
    raw: str  # the original reply string
    overload: bool = False
    overflow: bool = False

    def __str__(self) -> str:
        if self.overload:
            return f"OVLOAD ({self.unit})"
        if self.overflow:
            return f"OVFLOW ({self.unit})"
        return f"{self.value:.6g} {self.unit}"


def parse_reading(line: str) -> Reading:
    """Parse a READ?/READ2?/MM? sub-field per manual 16.2.1.

    Value is either ' d.ddddde[+-]dd' (space or '-', 6 digits, exponent)
    or the literal 'OVLOAD' / 'OVFLOW'. Unit is up to 8 chars (may contain
    spaces, e.g. 'V AC+DC').
    """
    raw = line
    line = line.strip()
    if not line:
        return Reading(None, "", raw, overload=True)

    # Overload / overflow
    m = re.match(r"^(OVLOAD|OVFLOW)\s*(.*)$", line, re.I)
    if m:
        word, unit = m.group(1).upper(), m.group(2).strip()
        return Reading(
            None,
            unit,
            raw,
            overload=(word == "OVLOAD"),
            overflow=(word == "OVFLOW"),
        )

    # Number then unit. The number is a fixed-width field (11 chars),
    # unit follows. Easiest: split on first whitespace *after* the number.
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s+(.*)$", line, re.I)
    if not m:
        raise ValueError(f"unrecognised reading: {raw!r}")
    value = float(m.group(1))
    unit = m.group(2).strip()
    return Reading(value, unit, raw)


class TTI1908:
    """Thin synchronous wrapper around the 1908's SCPI-style command set."""

    def __init__(self, port: str = DEFAULT_PORT, timeout: float = 2.0):
        self.ser = serial.Serial(port, 9600, timeout=timeout)
        # Give the kernel a moment to finish setting up the tty (it is ignored
        # for a CDC-ACM node but this is cheap insurance against a race).
        time.sleep(0.1)
        # Aggressively drop anything the meter was mid-transmission on:
        # rare firmware glitches (hit by sending LIMITS in some modes) leave
        # the 1908 streaming binary frames until *CLS or *RST.
        self._drain_junk()

    def _drain_junk(self) -> None:
        """Swallow any pending bytes so the next query starts clean."""
        self.ser.reset_input_buffer()
        # Small extra wait + re-drain in case bytes were still arriving.
        time.sleep(0.2)
        if self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    # -- low level ----------------------------------------------------
    def write(self, cmd: str) -> None:
        """Send one command, no response expected."""
        self.ser.write(cmd.encode("ascii") + b"\n")

    def _is_ascii_line(self, payload: bytes) -> bool:
        return all(32 <= b < 127 or b == 9 for b in payload)

    def query(self, cmd: str) -> str:
        """Send a query and return the response stripped of CR LF.

        Robustness measures:

        * Pending garbage from a previous buggy command is silently dropped
          (the 1908 sometimes emits \xc1 / \x05 binary frames after rapid
          mode/speed changes).
        * We read CR-LF-terminated lines and skip past any that are not
          clean printable ASCII, until we get a valid reply or time out.
        """
        import os

        verbose = os.environ.get("TTI1908_TRACE")
        # Pre-drain so no stale garbage gets attributed to this query.
        if self.ser.in_waiting:
            stale = self.ser.read(self.ser.in_waiting)
            if verbose:
                print(f"[trace] {cmd!r} pre-drain={stale!r}", flush=True)
        self.write(cmd)
        deadline = time.monotonic() + max(self.ser.timeout or 2.0, 2.0)
        while time.monotonic() < deadline:
            line = self.ser.read_until(b"\r\n", size=4096)
            if verbose:
                print(f"[trace] {cmd!r} raw={line!r}", flush=True)
            if not line.endswith(b"\r\n"):
                # Timed out on this read; keep trying within overall deadline.
                if line:
                    continue
                break
            payload = line[:-2]
            if self._is_ascii_line(payload):
                return payload.decode("ascii")
        raise TimeoutError(f"no clean reply to {cmd!r} within timeout")

    # -- common commands (16.2.7) -------------------------------------
    def idn(self) -> str:
        """Raw *IDN? response: '<vendor>, <model>, <serial>, <firmware>'."""
        return self.query("*IDN?")

    def identify(self) -> tuple[str, str, str, str]:
        """Parsed *IDN? response: (vendor, model, serial, firmware_version).

        The 1908 leaves the <serial> field empty on USB; it appears as "".
        """
        parts = [p.strip() for p in self.idn().split(",")]
        # Pad to 4 just in case a field is missing.
        while len(parts) < 4:
            parts.append("")
        return tuple(parts[:4])  # type: ignore[return-value]

    def firmware_version(self) -> str:
        """Return just the firmware revision string, e.g. '1.07'."""
        return self.identify()[3]

    def reset(self) -> None:
        self.write("*RST")

    def opc(self) -> bool:
        return self.query("*OPC?") == "1"

    def self_test(self) -> int:
        """Returns 0 (the 1908 has no self-test and always reports 0)."""
        return int(self.query("*TST?"))

    def cls(self) -> None:
        """Clear status registers."""
        self.write("*CLS")

    # -- main display readouts (16.2.1) -------------------------------
    def read(self) -> Reading:
        """READ? — one reading from the main display."""
        return parse_reading(self.query("READ?"))

    def read2(self) -> Reading | str:
        """READ2? — one reading from the secondary display, or 'RANGE'
        if the secondary display is currently showing the main range."""
        reply = self.query("READ2?")
        if reply.strip().upper() == "RANGE":
            return "RANGE"
        return parse_reading(reply)

    def mode(self) -> tuple[str, str, str]:
        """MODE? — (function, range, MAN/AUTO)."""
        parts = [p.strip() for p in self.query("MODE?").split(",")]
        if len(parts) != 3:
            raise ValueError(f"unexpected MODE? reply: {parts!r}")
        return tuple(parts)  # type: ignore[return-value]

    def mode2(self) -> tuple[str, str, str]:
        parts = [p.strip() for p in self.query("MODE2?").split(",")]
        if len(parts) != 3:
            raise ValueError(f"unexpected MODE2? reply: {parts!r}")
        return tuple(parts)  # type: ignore[return-value]

    # -- measurement functions (16.2.2) -------------------------------
    def vdc(self, rng: str | None = None) -> None:
        self.write("VDC" if rng is None else f"VDC {rng}")

    def vac(self, rng: str | None = None) -> None:
        self.write("VAC" if rng is None else f"VAC {rng}")

    def idc(self, rng: str | None = None) -> None:
        self.write("IDC" if rng is None else f"IDC {rng}")

    def iac(self, rng: str | None = None) -> None:
        self.write("IAC" if rng is None else f"IAC {rng}")

    def ohms(self, rng: str | None = None, four_wire: bool = False) -> None:
        cmd = "4WOHMS" if four_wire else "2WOHMS"
        self.write(cmd if rng is None else f"{cmd} {rng}")

    def diode(self) -> None:
        self.write("DIODE")

    def continuity(self) -> None:
        self.write("CONT")

    def cap(self, rng: str | None = None) -> None:
        self.write("CAP" if rng is None else f"CAP {rng}")

    def freq(self, rng: str | None = None) -> None:
        self.write("FREQ" if rng is None else f"FREQ {rng}")

    def temp_c(self, probe: str | None = None) -> None:
        self.write("TEMPC" if probe is None else f"TEMPC {probe}")

    def autorange(self) -> None:
        self.write("AUTO")

    def manual_range(self) -> None:
        self.write("MAN")

    # -- filter / sampling (16.2.1) -----------------------------------
    def filter(self, on: bool) -> None:
        """Enable/disable the 50/60 Hz filter (affects Vdc and Ohms only)."""
        self.write("FILTON" if on else "FILTOFF")

    def speed(self, fast: bool) -> None:
        """Sampling rate: fast=20 SPS, slow=4 SPS."""
        self.write("SPEED FAST" if fast else "SPEED SLOW")

    # -- modifiers (16.2.4/5) -----------------------------------------
    def hold(self, on: bool = True) -> None:
        self.write("HOLD" if on else "HOLD OFF")

    def null(self, on: bool = True) -> None:
        self.write("NULL" if on else "NULLOFF")

    def min_max_start(self) -> None:
        self.write("MMON")

    def min_max(self) -> tuple[Reading, Reading]:
        """MM? — returns (min, max). Fields are separated by two spaces."""
        reply = self.query("MM?")
        # manual: "two character strings separated by 2 spaces"
        # but each string contains a single internal space (value + unit).
        # Split on exactly two spaces.
        parts = reply.split("  ")
        if len(parts) != 2:
            # fall back: split in the middle
            parts = re.split(r"\s{2,}", reply, maxsplit=1)
            if len(parts) != 2:
                raise ValueError(f"unexpected MM? reply: {reply!r}")
        return parse_reading(parts[0]), parse_reading(parts[1])

    def limits(self, lo: float, hi: float) -> None:
        self.write(f"LIMITS {lo},{hi}")

    def limits_result(self) -> str:
        """LIMITS? — PASS / LOW / HIGH / OFF."""
        return self.query("LIMITS?").strip().upper()

    def cancel_modifier(self) -> None:
        self.write("CANCEL")

    # -- status (16.2.8) ----------------------------------------------
    def eer(self) -> int:
        """Query + clear Execution Error Register."""
        return int(self.query("EER?"))

    def qer(self) -> int:
        """Query + clear Query Error Register."""
        return int(self.query("QER?"))

    def stb(self) -> int:
        return int(self.query("*STB?"))

    def itr(self) -> int:
        return int(self.query("ITR?"))

    # -- teardown ------------------------------------------------------
    def close(self) -> None:
        if self.ser.is_open:
            self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
