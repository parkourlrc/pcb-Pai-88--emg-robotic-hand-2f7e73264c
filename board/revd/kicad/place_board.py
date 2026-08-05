#!/usr/bin/env python3
"""Rebuild the EMG rev E board: add the servo parts, re-place everything
into a 100 x 100 mm outline, and re-apply connectivity from the netlist.

Run with KiCad's bundled interpreter, which is the one that has pcbnew:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
Versions/3.9/bin/python3 place_board.py

Placement rationale (this is the part that determines noise performance,
so it is deliberate rather than automatic):

  * The board splits left-to-right into three zones that never overlap:
        x < 34    digital  - the Pico and nothing else
        x 34..96  analog   - the whole signal chain, at ±9 V
        y > 64    power/servo - high current, kept off the analog side
  * Signal flows RIGHT to LEFT: the electrodes enter at the right edge
    (J1), through the in-amp, the gain and filter stages, and finally
    reach the Pico's ADC pin on the Pico's left-hand pad column. That
    keeps every sensitive node short and stops the ADC trace from having
    to cross the digital section.
  * The Pico is oriented with pin 1 at top-left, so its USB connector
    faces the top edge and is reachable with the board mounted.
  * GP0..GP4 sit on the Pico's outer column, so the five servo signals
    exit downward into the servo block without crossing analog copper.
  * The DRL network sits next to J1 because DRL_REF drives an electrode:
    that loop wants to be physically small.
  * Servo return current is confined to the bottom-right block and only
    meets analog ground at R15.
"""
import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "emg.kicad_pcb")
NETLIST = os.path.join(HERE, "emg_new.net")

KICAD_FP = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
LOCAL_FP = os.path.join(HERE, "emg.pretty")

BOARD_W = 100.0
BOARD_H = 100.0
MARGIN = 4.0

# ---------------------------------------------------------------- placement
# ref: (x, y, rotation_degrees).  Coordinates are the footprint origin.
PLACE = {
    # ---- digital: the Pico, pin 1 top-left, USB facing the top edge.
    #      Pad column 1 (GP0..GP4 -> servos) faces the left edge; pad
    #      column 21..40 (ADC0, 3V3, GND) faces the analog section.
    # Nudged right of centre to widen the escape channel between the
    # Pico's left pad column and the board edge - all five servo GPIO
    # leave from that column and have to funnel down it.
    "J2":  (19.0, 36.0, 0),

    # ---- ADC entry + clamp diodes, immediately beside Pico pad 31
    "R10": (38.0, 34.86, 0),
    "D1":  (33.0, 25.0, 90),
    "D2":  (33.0, 42.0, 90),

    # ---- MCP6004 (all four op-amps) central, with its decoupling
    "U2":  (50.0, 32.0, 0),
    "C6":  (40.0, 20.0, 0),

    # ---- low-pass then buffer, feeding leftward toward the ADC
    "R6":  (42.0, 48.0, 0),
    "C4":  (54.0, 48.0, 0),

    # ---- gain stage
    "R5":  (66.0, 24.0, 0),
    "R4":  (66.0, 30.0, 0),
    "R3":  (66.0, 36.0, 0),
    "C3":  (70.0, 42.0, 0),

    # ---- instrumentation amp and its decoupling, near the electrodes
    "U1":  (64.0, 12.0, 0),
    "R9":  (50.0, 12.0, 0),   # AD623 gain resistor (RG on the datasheet)
    "C1":  (78.0, 20.0, 0),
    "C2":  (78.0, 26.0, 0),
    "R1":  (80.0, 8.0, 0),
    "R2":  (80.0, 14.0, 0),

    # ---- electrode connector at the right edge
    "J1":  (93.0, 14.0, 0),

    # ---- driven right leg. Ordered by how badly each part wants to be near
    #      J1: R11/R12 sense EL_P/EL_N and R14 drives DRL_REF, so all three
    #      touch the electrode connector and sit closest. R13/C7 only wrap
    #      U2C and can sit furthest away.
    "R11": (88.0, 32.0, 0),
    "R12": (88.0, 38.0, 0),
    "R14": (88.0, 44.0, 0),
    "R13": (88.0, 50.0, 0),
    "C7":  (88.0, 56.0, 0),

    # ---- VMID reference
    "R7":  (61.0, 46.0, 90),
    "R8":  (61.0, 60.0, 90),
    "C5":  (68.0, 54.0, 0),

    # ---- bipolar supply entry, analog side
    "J4":  (88.0, 66.0, 0),

    # ---- servo block along the bottom, clear of the analog chain
    "J5":  (14.0, 88.0, 0),
    "J6":  (22.0, 88.0, 0),
    "J7":  (30.0, 88.0, 0),
    "J8":  (38.0, 88.0, 0),
    "J9":  (46.0, 88.0, 0),
    "J10": (60.0, 88.0, 0),
    "C8":  (74.0, 88.0, 0),
    # R15 stands vertical and STRADDLES the SERVO_GND pour edge at y=82:
    # its SERVO_GND pad must land inside the servo strip and its GND pad
    # outside it, in analog copper. Laid flat inside the strip, the GND
    # pad had no GND copper to bond to and DRC reported it unconnected.
    "R15": (88.0, 84.0, 90),

    # ---- mounting holes
    "H1":  (5.0, 5.0, 0),
    "H2":  (95.0, 5.0, 0),
    "H3":  (5.0, 95.0, 0),
    "H4":  (95.0, 95.0, 0),
}

# footprints that must be added (present in the netlist, absent on the board)
FP_LIB = {
    "Capacitor_THT":                 os.path.join(KICAD_FP, "Capacitor_THT.pretty"),
    "Connector_PinHeader_2.54mm":    os.path.join(KICAD_FP, "Connector_PinHeader_2.54mm.pretty"),
    "TerminalBlock_Phoenix":         os.path.join(KICAD_FP, "TerminalBlock_Phoenix.pretty"),
    "Resistor_THT":                  os.path.join(KICAD_FP, "Resistor_THT.pretty"),
    "MountingHole":                  os.path.join(KICAD_FP, "MountingHole.pretty"),
    "emg":                           LOCAL_FP,
}


# ---------------------------------------------------------------- netlist
def tokenize(src):
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "()":
            out.append(c); i += 1
        elif c == '"':
            j = i + 1; buf = []
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    buf.append(src[j + 1]); j += 2
                else:
                    buf.append(src[j]); j += 1
            out.append(("STR", "".join(buf))); i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not src[j].isspace() and src[j] not in '()"':
                j += 1
            out.append(("SYM", src[i:j])); i = j
    return out


def parse(tokens):
    def build(pos):
        node = []
        while pos < len(tokens):
            t = tokens[pos]
            if t == "(":
                child, pos = build(pos + 1)
                node.append(child)
            elif t == ")":
                return node, pos + 1
            else:
                node.append(t[1]); pos += 1
        return node, pos
    tree, _ = build(1)
    return tree


def find(node, tag):
    for c in node:
        if isinstance(c, list) and c and c[0] == tag:
            yield c


def val(node, tag, default=None):
    for c in find(node, tag):
        return c[1] if len(c) > 1 else default
    return default


def read_netlist(path):
    tree = parse(tokenize(open(path).read()))
    comps, nets = {}, {}
    for blk in find(tree, "components"):
        for comp in find(blk, "comp"):
            comps[val(comp, "ref")] = {
                "value": val(comp, "value") or "",
                "footprint": val(comp, "footprint") or "",
            }
    for blk in find(tree, "nets"):
        for net in find(blk, "net"):
            name = val(net, "name")
            for nd in find(net, "node"):
                nets.setdefault(name, []).append((val(nd, "ref"), val(nd, "pin")))
    return comps, nets


# ---------------------------------------------------------------- helpers
def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def main():
    comps, nets = read_netlist(NETLIST)
    board = pcbnew.LoadBoard(BOARD)
    existing = {f.GetReference(): f for f in board.GetFootprints()}
    print("loaded board: %d footprints" % len(existing))

    # ---- reference renames.
    # RG was renamed to R9: KiCad treats a reference with no numeric suffix
    # as unannotated, so it showed up as "RG?" in the BOM and a GUI
    # re-annotate could have renumbered it and desynced board from schematic.
    # It is the same physical part, so rename it in place. Deleting and
    # re-adding also works on paper but leaves pcbnew handing back stale
    # SWIG handles for the rest of the run.
    RENAME = {"RG": "R9"}
    for old_ref, new_ref in RENAME.items():
        fp = existing.pop(old_ref, None)
        if fp is not None and new_ref not in existing:
            fp.SetReference(new_ref)
            existing[new_ref] = fp
            print("  ~ renamed %s -> %s" % (old_ref, new_ref))

    # Anything still on the board but absent from the netlist is an orphan.
    # Mounting holes are exempt - they are board-only by design. Report
    # rather than delete, so a real mistake is visible instead of silent.
    orphans = [r for r in sorted(set(existing) - set(comps))
               if not r.startswith("H")]
    if orphans:
        print("  !! orphan footprints not in the netlist: %s" % ", ".join(orphans))

    # ---- add any footprint that the netlist has and the board lacks
    for ref, info in sorted(comps.items()):
        if ref in existing:
            continue
        libname, fpname = info["footprint"].split(":", 1)
        libpath = FP_LIB.get(libname)
        if libpath is None:
            print("  !! no library path for %s (%s)" % (libname, ref)); continue
        fp = pcbnew.FootprintLoad(libpath, fpname)
        if fp is None:
            print("  !! failed to load %s:%s for %s" % (libname, fpname, ref)); continue
        fp.SetReference(ref)
        fp.SetValue(info["value"])
        # FootprintLoad() returns the footprint with a bare FPID (no library
        # nickname), which makes it read as "doesn't match the symbol's
        # footprint" in a schematic-parity check and would let a future
        # "update PCB from schematic" swap the part out. Restore the full id.
        fp.SetFPID(pcbnew.LIB_ID(libname, fpname))
        board.Add(fp)
        existing[ref] = fp
        print("  + added %-5s %s" % (ref, info["footprint"]))

    # ---- re-apply connectivity from the netlist
    for name in sorted(nets):
        if board.FindNet(name) is None:
            board.Add(pcbnew.NETINFO_ITEM(board, name))
    assigned = 0
    for name, nodes in nets.items():
        netinfo = board.FindNet(name)
        for ref, pin in nodes:
            fp = existing.get(ref)
            if fp is None:
                print("  !! netlist references missing footprint %s" % ref); continue
            pad = fp.FindPadByNumber(str(pin))
            if pad is None:
                print("  !! %s has no pad %s" % (ref, pin)); continue
            pad.SetNet(netinfo)
            assigned += 1
    print("assigned %d pads across %d nets" % (assigned, len(nets)))

    # ---- place.
    # PLACE holds the desired CENTRE of each part. Footprint origins sit
    # wherever the library author put them (pad 1 for the axial resistors
    # and the DIPs, the body centre for the discs), so rotate first, then
    # measure the origin-to-centre offset and correct for it. Placing by
    # origin instead is what put R11..R15 over the board edge.
    placed = 0
    for ref, (x, y, rot) in PLACE.items():
        fp = existing.get(ref)
        if fp is None:
            print("  !! nothing to place for %s" % ref); continue
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(vec(0, 0))
        bb = fp.GetBoundingBox(False, False)
        off_x = (bb.GetX() + bb.GetWidth() / 2.0) / 1e6
        off_y = (bb.GetY() + bb.GetHeight() / 2.0) / 1e6
        fp.SetPosition(vec(x - off_x, y - off_y))
        placed += 1
    print("placed %d footprints (centre-referenced)" % placed)

    # R15 is the single star-ground link. Pick the rotation that puts its
    # SERVO_GND pad on the servo side of y=82 and its GND pad on the analog
    # side; which physical end each pad lands on depends on the footprint's
    # internal pad order, so verify rather than assume.
    r15 = existing.get("R15")
    if r15 is not None:
        def pad_y(name):
            for pad in r15.Pads():
                if pad.GetNetname() == name:
                    return pad.GetPosition().y / 1e6
            return None
        if (pad_y("SERVO_GND") or 0) < (pad_y("GND") or 0):
            r15.SetOrientationDegrees(270)
            print("R15: flipped to 270 so SERVO_GND sits inside the servo strip")
        print("R15 straddle: SERVO_GND pad y=%.2f, GND pad y=%.2f (boundary 82.0)"
              % (pad_y("SERVO_GND"), pad_y("GND")))
    missing = sorted(set(existing) - set(PLACE))
    if missing:
        print("  !! no placement entry for: %s" % ", ".join(missing))

    # ---- silkscreen tidy-up.
    # The old board's text was positioned for the 150x125 layout, which left
    # J1's value string sitting on top of the Pico. Hide values (the BOM
    # carries them) and park each reference just above its own footprint.
    for ref, fp in existing.items():
        fp.Value().SetVisible(False)
        r = fp.Reference()
        r.SetVisible(not ref.startswith("H"))
        r.SetLayer(pcbnew.F_SilkS)
        r.SetTextSize(pcbnew.VECTOR2I(mm(1.0), mm(1.0)))
        r.SetTextThickness(mm(0.15))
        bb = fp.GetBoundingBox(False, False)
        cx = (bb.GetX() + bb.GetWidth() / 2.0) / 1e6
        top = bb.GetY() / 1e6
        r.SetPosition(vec(cx, top - 1.1))
        r.SetTextAngleDegrees(0)

    # Drop every pre-existing board text. All of it was positioned for the
    # 150x125 layout: three items now sit off the board entirely and
    # "EL+ EL- REF" had landed on top of the Pico.
    dropped = 0
    for d in list(board.GetDrawings()):
        if "TEXT" in d.GetClass().upper():
            board.Remove(d); dropped += 1

    def silk(txt, x, y, size=1.2, just=pcbnew.GR_TEXT_H_ALIGN_CENTER):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(txt)
        t.SetLayer(pcbnew.F_SilkS)
        t.SetPosition(vec(x, y))
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(size / 6.0))
        t.SetHorizJustify(just)
        board.Add(t)

    # ---- board identification and the safety warning
    silk("EMG FRONT END  rev E", 50.0, 68.0, 2.0)
    silk("BATTERY POWER ONLY - no mains while worn", 50.0, 72.0, 1.4)
    silk("SERVO GND joins analog GND only at R15", 50.0, 76.0, 1.2)

    # ---- per-pad legends on every connector, so nothing is wired by guesswork
    SHORT = {"EL_P": "EL+", "EL_N": "EL-", "DRL_REF": "REF", "+9V": "+9",
             "-9V": "-9", "GND": "GND", "+5VSRV": "+5V", "SERVO_GND": "GND",
             "SRV1": "SIG", "SRV2": "SIG", "SRV3": "SIG", "SRV4": "SIG",
             "SRV5": "SIG"}
    # (ref, dx, dy, justify) - where the label sits relative to each pad
    # Per-pad labels only where the pads are far enough apart to take them.
    # J4 and J10 are on 2.54 mm pitch, so three 1 mm strings side by side
    # overlapped each other and the connector outline; those two get a single
    # combined string underneath instead.
    LEGEND = {
        "J1":  (-3.2, 0.0, pcbnew.GR_TEXT_H_ALIGN_RIGHT),
    }
    for ref, (dx, dy, just) in LEGEND.items():
        fp = existing.get(ref)
        if fp is None:
            continue
        for pad in fp.Pads():
            name = SHORT.get(pad.GetNetname())
            if not name:
                continue
            p = pad.GetPosition()
            silk(name, p.x / 1e6 + dx, p.y / 1e6 + dy, 1.0, just)

    for ref, text, dy in (("J4", "+9  GND  -9", 5.0),
                          ("J10", "+5V   GND", 5.4)):
        fp = existing.get(ref)
        if fp is None:
            continue
        bb = fp.GetBoundingBox(False, False)
        cx = (bb.GetX() + bb.GetWidth() / 2.0) / 1e6
        cy = (bb.GetY() + bb.GetHeight() / 2.0) / 1e6
        silk(text, cx, cy + dy, 1.0)

    # servo row: one legend for the column order, then the channel per header
    silk("SERVO OUT   pin1 GND / pin2 +5V / pin3 SIG", 30.0, 79.5, 1.1)
    for i, ref in enumerate(("J5", "J6", "J7", "J8", "J9"), start=1):
        fp = existing.get(ref)
        if fp is None:
            continue
        bb = fp.GetBoundingBox(False, False)
        cx = (bb.GetX() + bb.GetWidth() / 2.0) / 1e6
        bot = (bb.GetY() + bb.GetHeight()) / 1e6
        silk("SRV%d" % i, cx, bot + 1.6, 1.0)

    print("silkscreen: %d stale texts removed, refs re-parked, legends added"
          % dropped)

    # ---- overlap + off-board report, so placement is checked, not assumed
    boxes = {}
    for ref, fp in existing.items():
        bb = fp.GetBoundingBox(False, False)   # copper+courtyard, no text
        boxes[ref] = (bb.GetX() / 1e6, bb.GetY() / 1e6,
                      (bb.GetX() + bb.GetWidth()) / 1e6,
                      (bb.GetY() + bb.GetHeight()) / 1e6)
    refs = sorted(boxes)
    clashes = []
    for i, a in enumerate(refs):
        ax1, ay1, ax2, ay2 = boxes[a]
        for b in refs[i + 1:]:
            bx1, by1, bx2, by2 = boxes[b]
            ox = min(ax2, bx2) - max(ax1, bx1)
            oy = min(ay2, by2) - max(ay1, by1)
            if ox > 0 and oy > 0:
                clashes.append((a, b, ox, oy))
    off = []
    for ref, (x1, y1, x2, y2) in boxes.items():
        # mounting holes are meant to hug the corners; they only have to
        # clear the board edge, not the component keep-in margin.
        m = 0.5 if ref.startswith("H") else MARGIN
        if x1 < m or y1 < m or x2 > BOARD_W - m or y2 > BOARD_H - m:
            off.append((ref, x1, y1, x2, y2))

    print("\n--- placement check ---")
    if clashes:
        print("OVERLAPS (%d):" % len(clashes))
        for a, b, ox, oy in sorted(clashes, key=lambda c: -c[2] * c[3]):
            print("   %-4s <-> %-4s  by %.2f x %.2f mm" % (a, b, ox, oy))
    else:
        print("OVERLAPS: none")
    if off:
        print("OUTSIDE %.0fmm MARGIN (%d):" % (MARGIN, len(off)))
        for ref, x1, y1, x2, y2 in sorted(off):
            print("   %-4s bbox (%.1f, %.1f) .. (%.1f, %.1f)" % (ref, x1, y1, x2, y2))
    else:
        print("MARGIN: all inside")
    print("-----------------------\n")

    # ---- new board outline: 100 x 100 with chamfered corners
    for d in list(board.GetDrawings()):
        if d.GetLayer() == pcbnew.Edge_Cuts:
            board.Remove(d)
    ch = 5.0
    pts = [
        (ch, 0), (BOARD_W - ch, 0), (BOARD_W, ch), (BOARD_W, BOARD_H - ch),
        (BOARD_W - ch, BOARD_H), (ch, BOARD_H), (0, BOARD_H - ch), (0, ch),
    ]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(vec(*a))
        seg.SetEnd(vec(*b))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(0.15))
        board.Add(seg)
    print("outline set to %.0f x %.0f mm" % (BOARD_W, BOARD_H))

    # ---- rebuild the ground pours; the old ones still matched the 150x125
    #      outline and would have flooded past the new board edge.
    for i in reversed(range(board.GetAreaCount())):
        board.Remove(board.GetArea(i))
    inset = 0.5
    zpts = [
        (ch + inset, inset), (BOARD_W - ch - inset, inset),
        (BOARD_W - inset, ch + inset), (BOARD_W - inset, BOARD_H - ch - inset),
        (BOARD_W - ch - inset, BOARD_H - inset), (ch + inset, BOARD_H - inset),
        (inset, BOARD_H - ch - inset), (inset, ch + inset),
    ]
    gnd = board.FindNet("GND")
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        if gnd is not None:
            zone.SetNet(gnd)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in zpts:
            outline.Append(mm(x), mm(y))
        zone.SetLocalClearance(mm(0.3))
        zone.SetMinThickness(mm(0.25))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        board.Add(zone)
    print("rebuilt 2 GND pours to the new outline")

    pcbnew.SaveBoard(BOARD, board)
    print("saved %s" % BOARD)


if __name__ == "__main__":
    main()
