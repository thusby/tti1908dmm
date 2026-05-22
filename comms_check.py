"""
comms_check.py — "does the DMM talk to us at all?" canary.

Sends three queries (*IDN?, MODE?, READ?) via the driver class and prints
the results.  If this script exits cleanly the USB topology, group
membership, and serial driver are all working.

Usage:
    venv/bin/python comms_check.py

Failure modes to check if it errors:
    * No /dev/serial/by-id/usb-TTI_1908_DMM_*-if00 node
        → DMM not plugged in, or plugged directly into an xHCI port
          (must go through a USB 2.0 hub — see README.md)
    * PermissionError on the serial port
        → user is not in the 'dialout' group; run: newgrp dialout
    * TimeoutError on *IDN? or READ?
        → DMM is on but unresponsive; power-cycle and try again
"""

from tti1908 import TTI1908

with TTI1908() as dmm:
    print(f"IDN:   {dmm.idn()}")
    func, rng, rmode = dmm.mode()
    print(f"MODE:  {func}, {rng}, {rmode}")
    print(f"READ:  {dmm.read()}")
