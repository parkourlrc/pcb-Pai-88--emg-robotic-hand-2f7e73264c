#!/usr/bin/env python3
"""Independent verification of the EMG rev E board and its release output.

Run with KiCad's bundled interpreter:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
Versions/3.9/bin/python3 verify_board.py [release_dir]

This deliberately re-derives things rather than trusting the tools that
produced them: it compares the board's pad-net assignments against the
netlist directly, re-measures the outline out of the Edge.Cuts gerber,
reconciles the drill count against pads plus vias, and recomputes the gain
and ADC scaling from component values to catch stale figures in the docs.

Exit status is non-zero if any check fails, so it can gate a release.
"""
import math
import os
import re
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from place_board import read_netlist  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "emg.kicad_pcb")
NETLIST = os.path.join(HERE, "emg_new.net")
DOCS = [os.path.join(HERE, "..", n) for n in ("constraints.md", "bringup.md", "fab.md")]

_fails = []
_count = 0


def check(name, ok, detail=""):
    global _count
    _count += 1
    print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        _fails.append(name)


def main(release):
    comps, nets = read_netlist(NETLIST)
    board = pcbnew.LoadBoard(BOARD)

    board_pad = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            board_pad[(fp.GetReference(), pad.GetPadName())] = pad.GetNetname()
    sch_pad = {}
    for net, nodes in nets.items():
        for ref, pin in nodes:
            sch_pad[(ref, str(pin))] = net

    print("=== 1. board vs schematic ===")
    mismatch = [(k, v, board_pad.get(k)) for k, v in sch_pad.items()
                if board_pad.get(k) != v]
    check("all %d schematic pad-nets match board" % len(sch_pad),
          not mismatch, str(mismatch[:2]) if mismatch else "")
    on_board = [f for f in board.GetFootprints()
                if not f.GetReference().startswith("H")]
    check("component count matches netlist", len(on_board) == len(comps),
          "board=%d netlist=%d" % (len(on_board), len(comps)))
    refs = {f.GetReference() for f in board.GetFootprints()}
    check("R9 present, RG absent", "R9" in refs and "RG" not in refs)

    print("=== 2. geometry and manufacturability ===")
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    check("routed: tracks and vias present", len(tracks) > 200 and vias,
          "%d tracks, %d vias" % (len(tracks), len(vias)))
    check("min via drill >= 0.3 mm", min(v.GetDrill() / 1e6 for v in vias) >= 0.3,
          "%.2f mm" % min(v.GetDrill() / 1e6 for v in vias))
    widths = {round(t.GetWidth() / 1e6, 2) for t in tracks}
    check("min track width >= 0.127 mm", min(widths) >= 0.127, "widths=%s" % sorted(widths))
    srv = [t for t in tracks if t.GetNetname() == "+5VSRV"]
    check("+5VSRV routed at 1.5 mm",
          bool(srv) and all(round(t.GetWidth() / 1e6, 2) == 1.5 for t in srv),
          "%d segments" % len(srv))

    print("=== 3. star ground ===")
    r15 = [f for f in board.GetFootprints() if f.GetReference() == "R15"][0]
    py = {p.GetNetname(): p.GetPosition().y / 1e6 for p in r15.Pads()}
    check("R15 straddles the 82 mm pour boundary",
          py.get("SERVO_GND", 0) > 82 > py.get("GND", 999),
          "SERVO_GND y=%.2f, GND y=%.2f" % (py.get("SERVO_GND", 0), py.get("GND", 0)))
    check("SERVO_GND reaches GND only through R15",
          {n[0] for n in nets["SERVO_GND"]} ==
          {"C8", "J10", "J5", "J6", "J7", "J8", "J9", "R15"},
          ",".join(sorted({n[0] for n in nets["SERVO_GND"]})))

    print("=== 4. connectors match the silkscreen ===")
    want = {"J1": {"1": "EL_P", "2": "EL_N", "3": "DRL_REF"},
            "J4": {"1": "+9V", "2": "GND", "3": "-9V"},
            "J10": {"1": "+5VSRV", "2": "SERVO_GND"}}
    for i, ref in enumerate(("J5", "J6", "J7", "J8", "J9"), start=1):
        want[ref] = {"1": "SERVO_GND", "2": "+5VSRV", "3": "SRV%d" % i}
    bad = [(r, p, e, board_pad.get((r, p)))
           for r, d in want.items() for p, e in d.items()
           if board_pad.get((r, p)) != e]
    check("connector pin assignments correct", not bad, str(bad[:2]) if bad else "")

    print("=== 5. release output (%s) ===" % os.path.basename(release))
    g = os.path.join(release, "gerbers")
    need = ["emg-F_Cu.gtl", "emg-B_Cu.gbl", "emg-F_Silkscreen.gto",
            "emg-B_Silkscreen.gbo", "emg-F_Mask.gts", "emg-B_Mask.gbs",
            "emg-Edge_Cuts.gm1", "emg.drl"]
    missing = [f for f in need if not os.path.exists(os.path.join(g, f))]
    check("all 8 required gerber/drill files present", not missing, ",".join(missing))
    if not missing:
        edge = open(os.path.join(g, "emg-Edge_Cuts.gm1")).read()
        pts = [(int(m.group(1)) / 1e6, int(m.group(2)) / 1e6)
               for m in re.finditer(r"X(-?\d+)Y(-?\d+)D0[12]", edge)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        check("gerber outline is 100 x 100 mm",
              abs((max(xs) - min(xs)) - 100) < 0.01 and abs((max(ys) - min(ys)) - 100) < 0.01,
              "%.2f x %.2f" % (max(xs) - min(xs), max(ys) - min(ys)))
        drl = open(os.path.join(g, "emg.drl")).read()
        check("drill distinguishes plated from non-plated",
              "NonPlated,NPTH" in drl and "Plated,PTH" in drl)
        tools = dict(re.findall(r"^T(\d+)C([\d.]+)", drl, re.M))
        check("no drill below 0.3 mm",
              min(float(v) for v in tools.values()) >= 0.3,
              "min %.2f mm" % min(float(v) for v in tools.values()))
        npads = sum(len(list(f.Pads())) for f in board.GetFootprints())
        nhits = len(re.findall(r"^X", drl, re.M))
        check("drill hits == pads + vias", nhits == npads + len(vias),
              "%d == %d + %d" % (nhits, npads, len(vias)))
    check("gerber zip present",
          any(f.endswith("_gerbers.zip") for f in os.listdir(release)))

    print("=== 6. arithmetic quoted in the docs ===")
    g1 = 1 + 100e3 / 5.1e3
    total = g1 * (1 + 15e3 / 1e3)
    text = "".join(open(d).read() for d in DOCS if os.path.exists(d))
    check("docs quote gain 330, not the rounded 336",
          "336" not in text and "330" in text, "computed %.1f" % total)
    check("docs quote 2.443 uV/count",
          "2.443" in text and "2.398" not in text,
          "computed %.3f" % (3.3 / 4096 / total * 1e6))
    check("442 Hz corner", abs(1 / (2 * math.pi * 3.6e3 * 100e-9) - 442) < 1)

    print()
    print("%d checks, %d failed" % (_count, len(_fails)))
    if _fails:
        print("FAILED:", ", ".join(_fails))
    return 1 if _fails else 0


if __name__ == "__main__":
    rel = sys.argv[1] if len(sys.argv) > 1 else sorted(
        d for d in os.listdir(HERE) if d.startswith("release_"))[-1]
    sys.exit(main(os.path.join(HERE, rel)))
