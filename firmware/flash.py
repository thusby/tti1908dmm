#!/usr/bin/env python3
"""
Reverse-engineered Python flasher for the Aim-TTi 1908 DMM bootloader.

This replaces the (Windows-only) Flash.exe utility by speaking the same
HID protocol directly. The protocol was reverse-engineered from the
decompiled Flash.exe v4.0 (see investigation_artifacts/flash.il).

Usage
-----

  # 1. Put the DMM into bootloader mode:
  #      power off, hold USB UPDATE, power on, release after ~3s
  #      lsusb should then show:  103e:042e TIi. Firmware Update Required.
  #
  # 2. Dry-run: parse the HEX, print what it would send, touch nothing:
  #      python3 firmware/flash.py firmware/1908_ALL_109_103.hex --dry-run
  #
  # 3. Actually flash:
  #      python3 firmware/flash.py firmware/1908_ALL_109_103.hex

Bootloader device
-----------------
  VID  = 0x103E (TTi)
  PID  = 0x042E (bootloader), product string "TIi. Firmware Update Required."
  HID class, one interface
  EP 0x01 OUT interrupt, 64 bytes  (output reports -- 65 bytes with report ID)
  EP 0x81 IN  interrupt, 64 bytes  (input reports, but we only read 5 bytes)

Protocol (summarised from BuildInitialReport / BuildReport / BuildFinalReport
and the SendData loop in Flash.exe):

  - Output report is 65 bytes = [0x00 report_id] + [64 payload bytes].
  - Input report is 5 bytes: [report_id] + 4 bytes giving a big-endian
    block-counter acknowledgement.
  - First report after connect is the "initial" report -- 54 zero bytes
    plus a 10-byte header: TargetProcessor (u16, big-endian at 0x36..0x37),
    LowAddress (u32 BE at 0x38..0x3B), and TotalByteCount = HighAddr - LowAddr
    + 1 (u32 BE at 0x3C..0x3F).
  - Subsequent reports carry 64 bytes of firmware payload each, starting at
    LowAddress. Bytes past HighAddress are padded with 0xFF.
  - Each block (initial and data) increments an "expectedBuffer" counter
    starting at 1. The device acknowledges blocks by sending back that
    counter value as a big-endian integer on the input endpoint.
  - After the last payload report is acknowledged, the host sends a final
    report of 64 bytes all 0xFF (BuildFinalReport). The device then
    reboots into the new firmware.

Requires:
  pip install hidapi
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

try:
    import hid  # hidapi
except ImportError:
    sys.exit("Please install hidapi:  pip install hidapi")


VID = 0x103E
PID = 0x042E
BOOTLOADER_PRODUCT = "TIi. Firmware Update Required."

REPORT_SIZE = 64   # payload size
INPUT_SIZE  = 4    # bytes from device per ack (per HID report descriptor)
WRITE_ID    = 0x00 # HID report ID for output reports


# -------------------------------------------------------------- HEX parsing

@dataclass
class ProcessorData:
    target_processor: int = 0
    low_address:  int = 0x08F0D180   # = MaxTargetSize; shrinks as we see bytes
    high_address: int = 0x00000000
    # target is indexed by absolute address while parsing, then sliced
    # to [low_address .. high_address] inclusive after FinishedLoad().
    target: bytearray = field(default_factory=lambda: bytearray(0x08F0D180))
    _finished: bool = False
    _starts_at_low: bool = False

    MAX_SIZE = 0x08F0D180   # = 150000000 decimal (matches ProcessorData ctor)

    @property
    def is_empty(self) -> bool:
        # The processor is empty if no data bytes were recorded. The low
        # watermark will still be at its sentinel (either the original
        # 0xFFFFFFFF we start at, or the Flash.exe-compatible MaxTargetSize).
        return self.high_address == 0 and self.low_address >= self.MAX_SIZE

    def finished_load(self) -> None:
        """Trim target[] to just the [low..high] range. Matches FinishedLoad()."""
        if self.low_address <= 0:
            return
        length = self.high_address - self.low_address + 1
        self.target = bytearray(self.target[self.low_address:self.low_address + length])
        self._starts_at_low = True
        self._finished = True

    def byte_at(self, absolute_address: int) -> int:
        """Fetch a firmware byte by its *absolute* address in the target region."""
        if self._starts_at_low:
            return self.target[absolute_address - self.low_address]
        return self.target[absolute_address]


def parse_hex(path: str) -> list[ProcessorData]:
    """Parse a TTi-flavoured Intel HEX file into a list of ProcessorData."""
    processors: list[ProcessorData] = []
    current = ProcessorData()
    processors.append(current)
    address: int = 0  # extended linear address accumulator

    with open(path, "rt") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line:
                continue

            if line.startswith("#TTI#"):
                current.target_processor = int(line[5:9], 16)
                continue

            if not line.startswith(":"):
                raise ValueError(f"Unknown line: {line!r}")

            byte_count = int(line[1:3], 16)
            addr16     = int(line[3:7], 16)
            rec_type   = line[7:9]

            if rec_type == "00":
                # Data record
                full_addr = (address << 16) + addr16
                for j in range(byte_count):
                    b = int(line[9 + 2*j : 9 + 2*j + 2], 16)
                    pos = full_addr + j
                    current.target[pos] = b
                    if pos > current.high_address:
                        current.high_address = pos
                    if pos < current.low_address:
                        current.low_address = pos

            elif rec_type == "01":
                # End-of-file marker for THIS processor
                current.finished_load()
                # If there are more lines, start a new processor
                current = ProcessorData()
                processors.append(current)

            elif rec_type == "04":
                # Extended linear address: upper 16 bits
                address = int(line[9:13], 16)

            elif rec_type == "05":
                # Start linear address: 32-bit entry point (ignored)
                address = int(line[9:17], 16)   # note: field keeps name "address"
                # The real Flash.exe stores this in a field it later overwrites.
                # We don't need the entry point for programming, so treat as noop.

            elif rec_type in ("02", "03"):
                # Segment address / start segment (ignored)
                pass

            else:
                raise ValueError(f"Unknown record type {rec_type!r} in line: {line!r}")

    # Remove trailing empty ProcessorData (the one after the last :xx...01 record)
    while processors and processors[-1].is_empty:
        processors.pop()
    if not processors:
        raise ValueError("HEX file yielded no data")
    return processors


# -------------------------------------------------------------- Reports

def build_initial_report(p: ProcessorData) -> bytearray:
    """Build the 64-byte initial handshake report."""
    buf = bytearray(REPORT_SIZE)          # all zero
    total = p.high_address - p.low_address + 1

    buf[0x3F] =  total        & 0xFF
    buf[0x3E] = (total >> 8)  & 0xFF
    buf[0x3D] = (total >> 16) & 0xFF
    buf[0x3C] = (total >> 24) & 0xFF

    buf[0x3B] =  p.low_address        & 0xFF
    buf[0x3A] = (p.low_address >> 8)  & 0xFF
    buf[0x39] = (p.low_address >> 16) & 0xFF
    buf[0x38] = (p.low_address >> 24) & 0xFF

    buf[0x37] =  p.target_processor       & 0xFF
    buf[0x36] = (p.target_processor >> 8) & 0xFF
    return buf


def build_data_report(p: ProcessorData, addr: int) -> tuple[bytearray, int]:
    """Fill a 64-byte payload report starting at absolute address `addr`.

    Returns (report, next_addr). Bytes past HighAddress are padded with 0xFF.
    """
    buf = bytearray(REPORT_SIZE)
    for i in range(REPORT_SIZE):
        cur = addr + i
        if cur <= p.high_address:
            buf[i] = p.byte_at(cur)
        else:
            buf[i] = 0xFF
    return buf, addr + REPORT_SIZE


def build_final_report() -> bytearray:
    """64 bytes of 0xFF sent after the last ACK to signal "done" / reboot."""
    return bytearray([0xFF] * REPORT_SIZE)


# -------------------------------------------------------------- Transport

class DryRunDevice:
    """Stand-in HID device that records what would have been sent.

    Matches real device semantics: every write advances a single counter
    by 1, and reads return the current counter once (then empty, then
    the counter again on the next poll). The caller's read_ack drains-to-
    latest loop handles this correctly.
    """
    def __init__(self):
        self.writes: list[bytes] = []
        self._ack = 0
        self._just_updated = True   # emit counter once on first read
    def write(self, data):
        payload = bytes(data)
        self.writes.append(payload)
        self._ack += 1
        self._just_updated = True
        return len(data)
    def read(self, length, timeout_ms=0):
        # Mimic interrupt IN endpoint: report counter continuously.
        # We alternate between "return counter" and "return empty" so the
        # caller's drain loop sees exactly one fresh value per poll cycle.
        if self._just_updated:
            self._just_updated = False
            data = bytearray(length)
            data[0] = (self._ack >> 24) & 0xFF
            data[1] = (self._ack >> 16) & 0xFF
            data[2] = (self._ack >> 8)  & 0xFF
            data[3] =  self._ack        & 0xFF
            return bytes(data[:INPUT_SIZE])
        self._just_updated = True
        return b""
    def set_nonblocking(self, flag):
        pass
    def close(self):
        pass


def open_bootloader() -> hid.device:
    """Find the 1908 bootloader HID device, open it, return a hid.device."""
    matches = [d for d in hid.enumerate(VID, PID)]
    if not matches:
        raise RuntimeError(
            f"No {VID:04x}:{PID:04x} HID device found. "
            f"Put the DMM into bootloader mode (hold USB UPDATE while powering on)."
        )
    path = matches[0]["path"]
    dev = hid.device()
    dev.open_path(path)
    # The device emits status reports at 1 kHz on an interrupt IN endpoint.
    # Non-blocking reads let us drain the stale reports in the OS queue and
    # read only the LATEST ack counter.
    dev.set_nonblocking(True)
    return dev


def write_report(dev, payload: bytearray) -> None:
    """Send a 64-byte HID output report.

    Note: the bootloader's HID descriptor has NO Report ID, so the wire
    payload is exactly 64 bytes. However, some hidapi builds on some
    platforms expect the caller to prepend a 0 byte anyway (the "no report
    id" convention). We try the descriptor-correct 64-byte write first and
    fall back to 65 bytes if the library complains.
    """
    assert len(payload) == REPORT_SIZE
    # On Linux hidapi (hidraw backend), writes always want the report id
    # byte prepended -- it's stripped before transmission if the descriptor
    # says "no report id". So we send 65 bytes: [0x00] + 64 data.
    full = bytes([WRITE_ID]) + bytes(payload)
    n = dev.write(full)
    if n != len(full):
        raise RuntimeError(f"short write: sent {n}/{len(full)}")


def _raw_read_ack(dev) -> int | None:
    """Drain the input queue and return the newest 4-byte counter value."""
    import time as _t
    last = None
    for _ in range(256):
        data = dev.read(INPUT_SIZE + 1)
        if not data:
            if last is not None:
                return last
            _t.sleep(0.001)
            continue
        b = bytes(data)
        if len(b) == INPUT_SIZE + 1:
            b = b[1:]
        if len(b) < INPUT_SIZE:
            continue
        last = (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]
    return last


def read_ack(dev, timeout_ms: int = 500, expected: int | None = None) -> int | None:
    """Drain the input queue until we see `expected` (or higher), or timeout.

    With `expected=None` just returns the current counter (blocks until at
    least one report is seen, or the timeout elapses).

    With an `expected` value, polls the interrupt IN endpoint repeatedly
    until the counter reaches that value or the timeout fires -- this is
    needed because there is a small delay (~1 ms) between a write landing
    on the device and the counter advancing in a new status report.
    """
    import time as _t
    deadline = _t.monotonic() + (timeout_ms / 1000.0)
    last = None
    while _t.monotonic() < deadline:
        cur = _raw_read_ack(dev)
        if cur is not None:
            last = cur
            if expected is None or cur >= expected:
                return cur
        _t.sleep(0.002)
    return last


# -------------------------------------------------------------- Main flow

def flash_one_processor(dev, p: ProcessorData, verbose: bool = True) -> None:
    """Send one ProcessorData's firmware to the device following the SendData flow."""
    total = p.high_address - p.low_address + 1
    if verbose:
        print(f"  Target processor : 0x{p.target_processor:04x}")
        print(f"  Low address      : 0x{p.low_address:08x}")
        print(f"  High address     : 0x{p.high_address:08x}")
        print(f"  Total bytes      : {total} ({total/1024:.1f} KB)")
        blocks = 1 + (total + REPORT_SIZE - 1) // REPORT_SIZE  # init + data blocks
        print(f"  HID reports      : {blocks}")

    # Sanity: what counter is the device reporting right now? It should be
    # 0 on a fresh bootloader entry, or whatever non-zero value accumulated
    # if we're retrying. We'll use this as the baseline and advance from there.
    baseline = read_ack(dev, timeout_ms=500)
    if baseline is None:
        baseline = 0
    if verbose:
        print(f"  baseline ack = {baseline}")

    # Initial report ---------------------------------------------
    init = build_initial_report(p)
    write_report(dev, init)
    expected_ack = baseline + 1
    ack = read_ack(dev, timeout_ms=2000, expected=expected_ack)
    if ack != expected_ack:
        raise RuntimeError(
            f"initial ack mismatch: got {ack}, expected {expected_ack} "
            f"(baseline was {baseline})"
        )
    if verbose:
        print(f"  [sent] initial report  (ack: {ack})")

    # Data reports -----------------------------------------------
    addr = p.low_address
    expected = expected_ack   # counter after the initial was acked
    t0 = time.monotonic()
    last_percent = -1

    while addr <= p.high_address:
        report, addr = build_data_report(p, addr)
        is_last = addr > p.high_address
        expected += 1
        write_report(dev, report)

        if is_last:
            # After receiving the final block, the bootloader resets its
            # counter to 0 as a completion signal (matches the isLast/
            # expectedBuffer=0 branch in Flash.exe SendData). Wait for
            # that transition -- we watch for the counter to either reach
            # `expected` briefly or drop back to 0.
            deadline = time.monotonic() + 5.0
            final_ack = None
            while time.monotonic() < deadline:
                cur = _raw_read_ack(dev)
                if cur is not None:
                    if cur == 0:
                        final_ack = 0
                        break
                    if cur == expected:
                        final_ack = cur  # transient; keep waiting for reset
                time.sleep(0.005)
            if final_ack != 0 and final_ack != expected:
                raise RuntimeError(
                    f"final block ack never settled: last seen {final_ack} "
                    f"(expected {expected} then 0)"
                )
            if verbose:
                print(f"\r  programming: 100% (final block acked, counter reset)")
            break

        # Normal (non-last) block: wait for counter == expected
        ack = read_ack(dev, timeout_ms=5000, expected=expected)
        if ack is None or ack != expected:
            raise RuntimeError(
                f"communications timeout waiting for ack {expected} "
                f"(last: {ack}) at addr 0x{addr:x}"
            )

        if verbose:
            done = min(addr, p.high_address + 1) - p.low_address
            pct = int(100 * done / total)
            if pct != last_percent:
                print(f"\r  programming: {pct:3d}% ", end="", flush=True)
                last_percent = pct

    if verbose:
        elapsed = time.monotonic() - t0
        print(f"\n  [done] processor flashed in {elapsed:.1f}s")


def flash(path: str, dry_run: bool = False) -> None:
    print(f"Reading {path}...")
    procs = parse_hex(path)
    print(f"Parsed {len(procs)} processor block(s):")
    for i, p in enumerate(procs):
        size = p.high_address - p.low_address + 1
        print(f"  [{i}] tp=0x{p.target_processor:04x}  "
              f"0x{p.low_address:08x}..0x{p.high_address:08x}  ({size} bytes)")

    if dry_run:
        dev = DryRunDevice()
        print("\n--- DRY RUN: no device traffic ---")
    else:
        print("\nOpening bootloader...")
        dev = open_bootloader()

    try:
        for i, p in enumerate(procs):
            print(f"\nFlashing processor {i+1}/{len(procs)}...")
            flash_one_processor(dev, p)

        # Final 64x 0xFF report
        final = build_final_report()
        if not dry_run:
            write_report(dev, final)
            time.sleep(0.010)
        else:
            dev.write(bytes([WRITE_ID]) + bytes(final))
        print("\n[sent] final report (device will reboot)")

    finally:
        if not dry_run:
            dev.close()

    if dry_run:
        print(f"\nDry run complete. Reports that would have been written: {len(dev.writes)}")
        print("First 5 reports, as hex:")
        for i, w in enumerate(dev.writes[:5]):
            print(f"  [{i}] {w.hex()}")
        print("Last report (final, should be 00+0xFF*64):")
        print(f"  [{len(dev.writes)-1}] {dev.writes[-1].hex()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("hexfile", help="TTi Intel HEX firmware file")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse and simulate, but do not talk to the device")
    args = ap.parse_args()
    flash(args.hexfile, dry_run=args.dry_run)
