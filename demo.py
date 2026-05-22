"""
Demo / smoke test for the TTi 1908 DMM.

Exercises the parts of the SCPI command set that have been verified to work
reliably on the 1908 over USB:

    * identification          (*IDN?, *OPC?, *TST?, *CLS, *RST)
    * main-display modes      (VDC, VAC, 2WOHMS, CAP, FREQ ...)
    * manual / auto ranging   (AUTO, MAN, explicit range arguments)
    * dual-display readout    (MODE2?, READ2?, FREQ2)
    * min / max recording     (MMON, MM?, CANCEL)
    * status queries          (EER?, *STB?, ITR?)

NOT exercised (they trigger firmware bugs on 1.07 that put the meter
into a binary-streaming state that only *RST can recover from):

    * LIMITS / LIMITS?        (never arms in tested modes; streams garbage)
    * SPEED FAST              (after FILTON: meter emits binary frames)
    * Data-logger commands    (LOGON, LOG?, TRIG, LOGCOUNT, LOGCLEAR)
    * AXB, WATTS, VA, DELTA   (require specific connection setups)

Run:  venv/bin/python demo.py
(ensure your user is in the 'dialout' group, or use sudo)
"""
from __future__ import annotations

import time

from tti1908 import TTI1908


def banner(msg: str) -> None:
    print(f"\n── {msg} ──")


def measure(dmm: TTI1908, label: str, settle: float = 0.6) -> None:
    time.sleep(settle)
    r = dmm.read()
    func, rng, rmode = dmm.mode()
    print(f"  {label:<24s} {str(r):<22s} [{func} {rng} {rmode}]")


def main() -> None:
    with TTI1908() as dmm:
        # ---------- identify and reset ----------
        banner("Identification")
        print(f"  IDN:       {dmm.idn()}")
        print(f"  *OPC?:     {dmm.opc()}")
        print(f"  *TST?:     {dmm.self_test()}  (1908 has no self-test)")
        dmm.cls()
        print(f"  *STB?:     {dmm.stb()}  (after *CLS)")
        print(f"  EER?:      {dmm.eer()}  (prior execution errors)")

        banner("*RST to factory defaults")
        dmm.reset()
        time.sleep(1.5)
        print(f"  mode now:  {dmm.mode()}")

        # ---------- DC volts ----------
        banner("DC Volts")
        dmm.vdc()
        measure(dmm, "open circuit, autorange")

        # ---------- resistance ----------
        banner("2-wire resistance")
        dmm.ohms()
        measure(dmm, "whatever is connected")

        banner("Resistance — forced 1 kΩ range")
        dmm.ohms("1000")
        measure(dmm, "manual 1 kΩ")
        dmm.autorange()
        measure(dmm, "back to autorange")

        # ---------- capacitance ----------
        banner("Capacitance (autorange)")
        dmm.cap()
        measure(dmm, "no cap connected", settle=1.2)

        # ---------- frequency ----------
        banner("Frequency (autorange)")
        dmm.freq()
        measure(dmm, "no signal")

        # ---------- dual display ----------
        banner("Dual display — Vac main, Freq secondary")
        dmm.vac()
        time.sleep(0.3)
        dmm.write("FREQ2")
        time.sleep(0.6)
        print(f"  MODE?   (main):      {dmm.mode()}")
        print(f"  MODE2?  (secondary): {dmm.mode2()}")
        print(f"  READ?   (main):      {dmm.read()}")
        print(f"  READ2?  (secondary): {dmm.read2()}")

        # ---------- min / max ----------
        banner("Min / Max — 3 s of Vdc")
        dmm.vdc()
        time.sleep(0.3)
        dmm.min_max_start()
        time.sleep(3.0)
        mn, mx = dmm.min_max()
        print(f"  min: {mn}")
        print(f"  max: {mx}")
        dmm.cancel_modifier()

# ---------- leave meter in a reasonable state ----------
        banner("Parking in Vdc autorange")
        dmm.vdc()
        dmm.autorange()
        print("\nDone.")


if __name__ == "__main__":
    main()
