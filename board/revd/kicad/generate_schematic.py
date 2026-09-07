#!/usr/bin/env python3
"""EMG front end, KiCad 7 schematic - clean-layout edition.

Conventions enforced everywhere:
- All text horizontal. Rotated symbols get counter-rotated properties
  (angle 270 for 90-deg symbols, verified empirically against kicad-cli).
- Series parts: REF left-above the body, VALUE right-above.
- Shunt parts: REF and VALUE stacked to the right of the body.
- Power symbols only at 0/180 (never 90/270, which renders sideways text).
- One signal baseline per stage, left to right.
- Junction dot at every T.
- Clamp diodes: Device:D pin1 = K (cathode). D1 K->+3V3, A->signal.
  D2 K->signal, A->GND. (The previous revision had both reversed.)
"""
import re
import pathlib
import uuid as _uuid

def _find_libdir():
    """Locate the stock symbol libraries.

    The original revision of this script was written against a Linux
    container (/usr/share/kicad/symbols, KiCad 7). On this Mac the libs
    ship inside the app bundle and use KiCad 10's tab-indented format, so
    both the path and the parser below have to cope with either.
    """
    for p in (
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
        "/usr/share/kicad/symbols",
    ):
        if pathlib.Path(p).is_dir():
            return pathlib.Path(p)
    raise SystemExit("cannot locate KiCad symbol libraries")


LIBDIR = _find_libdir()


def _extract(text, start):
    depth, i = 0, start
    while True:
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


def _raw(text, name):
    # KiCad 7 indents top-level symbols with two spaces, KiCad 10 with a tab.
    m = re.search(r'\n[ \t]+\(symbol "%s"\n' % re.escape(name), text)
    if not m:
        raise KeyError(name)
    return _extract(text, m.start() + 1)


def load_symbol(lib, name):
    text = (LIBDIR / f"{lib}.kicad_sym").read_text()
    blk = _raw(text, name)
    ext = re.search(r'\(extends "([^"]+)"', blk)
    if not ext:
        return blk
    parent = _raw(text, ext.group(1))
    merged = parent.replace(f'"{ext.group(1)}"', f'"{name}"', 1)
    merged = merged.replace(f'"{ext.group(1)}_', f'"{name}_')
    return re.sub(r'\(property "Value" "[^"]*"',
                  f'(property "Value" "{name}"', merged, count=1)


def pins_of(blk):
    out = {}
    for m in re.finditer(
        r'\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+) ([\d.]+)\)(.*?)\(number "([^"]+)"',
        blk, re.S,
    ):
        out[m.group(5)] = (float(m.group(1)), float(m.group(2)))
    return out


def uid():
    return str(_uuid.uuid4())


ROOT = uid()

# Block titles, captions and the dashed block outlines are all drawn in this
# blue, matching the reference sheet's boxed-block look.
BLUE = "20 60 176 1"

TRANSFORM = {                       # verified against kicad-cli netlists
    0:   lambda x, y, px, py: (x + px, y - py),
    90:  lambda x, y, px, py: (x - py, y - px),
    180: lambda x, y, px, py: (x - px, y + py),
    270: lambda x, y, px, py: (x + py, y + px),
}


class Sheet:
    # A block is a set of net-label-connected symbols/wires; nothing crosses
    # a block except by net name. That means a whole block can be translated
    # rigidly without changing connectivity. (ox, oy) is that per-block shift:
    # set it before authoring a block and every coordinate emitted while it is
    # set gets moved by the same amount, so the block lands on the layout grid
    # while its internal wiring stays intact.
    def __init__(self):
        self.libs, self.items = {}, []
        self.segs, self.juncs, self.pinpts = [], [], []
        self.ox, self.oy = 0.0, 0.0

    def at(self, block):
        """Set the active translation for the block about to be authored."""
        self.ox, self.oy = BLOCKS[block]

    def _need(self, lib, name):
        key = f"{lib}:{name}"
        if key not in self.libs:
            blk = load_symbol(lib, name)
            self.libs[key] = blk.replace(f'(symbol "{name}"', f'(symbol "{key}"', 1)
        return key

    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            a = (round(a[0] + self.ox, 2), round(a[1] + self.oy, 2))
            b = (round(b[0] + self.ox, 2), round(b[1] + self.oy, 2))
            if a != b:
                self.segs.append((a, b))

    def junction(self, p):
        self.juncs.append((round(p[0] + self.ox, 2), round(p[1] + self.oy, 2)))

    def _emit_wires(self):
        """Split every segment at any touching endpoint/junction/pin.

        KiCad connects wires only at shared endpoints -- an endpoint landing
        mid-segment is electrically open even under a junction dot. Splitting
        at every touch point makes each T a real endpoint join.
        """
        touch = set(self.juncs) | set(self.pinpts)
        for a, b in self.segs:
            touch.add(a); touch.add(b)
        out = []
        for a, b in self.segs:
            if a[0] == b[0]:                      # vertical
                lo, hi = sorted((a[1], b[1]))
                cuts = sorted({y for (x, y) in touch if x == a[0] and lo < y < hi})
                pts = [(a[0], lo)] + [(a[0], y) for y in cuts] + [(a[0], hi)]
            elif a[1] == b[1]:                    # horizontal
                lo, hi = sorted((a[0], b[0]))
                cuts = sorted({x for (x, y) in touch if y == a[1] and lo < x < hi})
                pts = [(lo, a[1])] + [(x, a[1]) for x in cuts] + [(hi, a[1])]
            else:
                pts = [a, b]
            for p1, p2 in zip(pts, pts[1:]):
                out.append(
                    f'  (wire (pts (xy {p1[0]} {p1[1]}) (xy {p2[0]} {p2[1]}))\n'
                    f'    (stroke (width 0) (type default)) (uuid {uid()})\n  )')
        for j in self.juncs:
            out.append(f'  (junction (at {j[0]} {j[1]}) '
                       f'(diameter 0) (color 0 0 0 0) (uuid {uid()}))')
        return out

    def label(self, p, text, rot=0, just="left"):
        p = (p[0] + self.ox, p[1] + self.oy)
        self.items.append(
            f'  (global_label "{text}" (shape bidirectional) '
            f'(at {round(p[0],2)} {round(p[1],2)} {rot}) (fields_autoplaced)\n'
            f'    (effects (font (size 1.27 1.27)) (justify {just}))\n'
            f'    (uuid {uid()})\n'
            f'    (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at 0 0 0)\n'
            f'      (effects (font (size 1.27 1.27)) hide)\n    )\n  )')

    def note(self, p, txt, size=1.3, bold=False):
        # All annotation text is blue, like the reference sheet: bold blue for
        # block titles, small blue for the captions underneath.
        b = " bold" if bold else ""
        x, y = round(p[0] + self.ox, 2), round(p[1] + self.oy, 2)
        self.items.append(
            f'  (text "{txt}" (at {x} {y} 0)\n'
            f'    (effects (font (size {size} {size}){b} (color {BLUE})) (justify left bottom))\n'
            f'    (uuid {uid()})\n  )')

    def nc(self, at):
        x, y = round(at[0] + self.ox, 2), round(at[1] + self.oy, 2)
        self.items.append(f'  (no_connect (at {x} {y}) (uuid {uid()}))')

    def box(self, x1, y1, x2, y2):
        x1, y1 = round(x1 + self.ox, 2), round(y1 + self.oy, 2)
        x2, y2 = round(x2 + self.ox, 2), round(y2 + self.oy, 2)
        self.items.append(
            f'  (rectangle (start {x1} {y1}) (end {x2} {y2})\n'
            f'    (stroke (width 0.2) (type dash) (color {BLUE})) (fill (type none)) (uuid {uid()})\n  )')

    def sym(self, lib, name, ref, value, at, angle=0, unit=1,
            refpos=None, valpos=None, hide_ref=False, fp=""):
        key = self._need(lib, name)
        x, y = at
        X, Y = x + self.ox, y + self.oy          # placed (translated) position
        pa = {0: 0, 90: 270, 180: 0, 270: 90}[angle]   # -> horizontal text
        refpos = refpos or (x + 2.3, y - 1.5)
        valpos = valpos or (x + 2.3, y + 1.1)
        props = [("Reference", ref, refpos, hide_ref),
                 ("Value", value, valpos, False),
                 ("Footprint", fp, (x, y), True),
                 ("Datasheet", "", (x, y), True)]
        ptxt = ""
        for pn, pv, (px, py), hd in props:
            h = " hide" if hd else ""
            ptxt += (f'    (property "{pn}" "{pv}" '
                     f'(at {round(px+self.ox,2)} {round(py+self.oy,2)} {pa})\n'
                     f'      (effects (font (size 1.27 1.27)) (justify left){h})\n    )\n')
        pins = pins_of(self.libs[key])
        pintxt = "".join(f'    (pin "{n}" (uuid {uid()}))\n' for n in pins)
        self.items.append(
            f'  (symbol (lib_id "{key}") (at {round(X,2)} {round(Y,2)} {angle}) (unit {unit})\n'
            f'    (in_bom yes) (on_board yes) (dnp no)\n'
            f'    (uuid {uid()})\n{ptxt}{pintxt}'
            f'    (instances (project "emg"\n'
            f'      (path "/{ROOT}" (reference "{ref}") (unit {unit}))\n    ))\n  )')
        tf = TRANSFORM[angle]
        # returned pins are LOCAL (untranslated) so block code wires in the same
        # frame it authored in; wire()/junction() re-apply (ox,oy). Stored pin
        # points are translated so the wire-splitting touch set is consistent.
        res = {n: tuple(round(v, 2) for v in tf(x, y, *p)) for n, p in pins.items()}
        self.pinpts.extend(tuple(round(v, 2) for v in tf(X, Y, *p))
                           for p in pins.values())
        return res

    # ---- convention helpers -------------------------------------------

    def series(self, ref, val, x, y, kind="R", fp=""):
        """Horizontal in-line part on baseline y. REF left-above, VALUE right-above."""
        return self.sym("Device", kind, ref, val, (x, y), 90,
                        refpos=(x - 6.0, y - 2.9), valpos=(x + 1.0, y - 2.9), fp=fp)

    def shunt(self, ref, val, x, y, kind="R", fp=""):
        """Vertical part. REF/VALUE stacked to the right."""
        return self.sym("Device", kind, ref, val, (x, y), 0,
                        refpos=(x + 2.3, y - 1.5), valpos=(x + 2.3, y + 1.1), fp=fp)

    def vdiode(self, ref, val, x, y, fp=""):
        """Vertical diode, cathode (pin 1) at TOP. Labels right."""
        return self.sym("Device", "D_Schottky", ref, val, (x, y), 270,
                        refpos=(x + 2.9, y - 1.5), valpos=(x + 2.9, y + 1.1), fp=fp)

    def power(self, kind, at, down=False):
        """Rail symbol at a wire end. down=True hangs the graphic downward."""
        key = self._need("power", kind)
        px, py = list(pins_of(self.libs[key]).values())[0]
        angle = 180 if down else 0
        tf = TRANSFORM[angle]
        gx, gy = tf(at[0], at[1], px, py)
        x, y = at[0] - (gx - at[0]), at[1] - (gy - at[1])
        vp = (x + 2.0, y + 2.2) if down else (x + 2.0, y - 2.2)
        self.sym("power", kind, f"#PWR{next(_pwr)}", kind,
                 (round(x, 2), round(y, 2)), angle,
                 refpos=(x, y), valpos=vp, hide_ref=True)


    def flag(self, at):
        """PWR_FLAG with its pin exactly at `at` (must lie on a wire)."""
        key = self._need("power", "PWR_FLAG")
        px, py = list(pins_of(self.libs[key]).values())[0]
        gx, gy = TRANSFORM[0](at[0], at[1], px, py)
        x, y = at[0] - (gx - at[0]), at[1] - (gy - at[1])
        self.sym("power", "PWR_FLAG", f"#FLG{next(_pwr)}", "PWR_FLAG",
                 (round(x, 2), round(y, 2)), 0,
                 refpos=(x, y), valpos=(x + 2.8, y - 5.2), hide_ref=True)


def _counter():
    i = 0
    while True:
        i += 1
        yield f"{i:03d}"


_pwr = _counter()


def render(sheet, path):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(
        f'(kicad_sch (version 20230121) (generator eeschema)\n'
        f'  (uuid {ROOT})\n  (paper "A3")\n'
        f'  (title_block\n    (title "EMG Front End + Acquisition")\n    (rev "E")\n'
        f'    (comment 1 "BATTERY POWER ONLY - never wear electrodes with mains equipment connected")\n'
        f'    (comment 2 "Gain 20.6 x 16 = 330 with R9=5.1k and R5=15k. Pico samples ADC0 at 1 kHz on a hardware timer.")\n'
        f'    (comment 3 "Servo output on its own +5V supply, star-grounded to analog GND through R15.")\n  )\n'
        f'  (lib_symbols\n' + "\n".join(sheet.libs.values()) + '\n  )\n'
        + "\n".join(sheet.items + sheet._emit_wires()) + '\n'
        f'  (sheet_instances\n    (path "/" (page "1"))\n  )\n)\n')



# Per-block translation (dx, dy) that arranges the ten functional blocks into a
# clean three-column grid (omega-style). Each block is authored in its own local
# frame below; s.at(name) shifts it into its grid cell. Blocks only ever connect
# by net name, so moving one never changes the circuit.
BLOCKS = {
    # column 1  (x ~ 14)          column 2  (x ~ 150)        column 3  (x ~ 286)
    "stage1":  (-3,  -26),        "stage23": (  7,  -25),    "acq":   ( 42, -107),
    "drl":     (-3,   -8),        "vmid":    ( 36,  -29),    "servo": ( 46, -110),
    "supply":  (-4,  -37),        "mcp":     ( 36,  -29),
    "flags":   (-3,  -36),        "outbuf":  (  2,  -29),
}

# ====================================================================== build (rev E)
s = Sheet()
B1 = 97.46

FP_R   = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
FP_C   = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm"
FP_C1U = "Capacitor_THT:C_Rect_L7.0mm_W2.0mm_P5.00mm"
FP_CEL = "Capacitor_THT:CP_Radial_D5.0mm_P2.50mm"
FP_DIP8 = "Package_DIP:DIP-8_W7.62mm"
FP_DIP14= "Package_DIP:DIP-14_W7.62mm"
FP_D   = "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal"
FP_J1  = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
FP_TERM= "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MPT-0,5-3-2.54_1x03_P2.54mm_Horizontal"

def T(x,y,t): s.note((x,y),t,2.1,bold=True)
def lab_stub(node, name, dx):   # short horizontal stub off a node -> global label
    end=(round(node[0]+dx,2), node[1]); s.wire(node,end)
    s.label(end, name, 0 if dx>0 else 180, "left" if dx>0 else "right"); return end

# ---------------------------------------------------------------- STAGE 1
s.at("stage1")
T(25,70,"STAGE 1  -  IN-AMP AD623  (G = 20.6)")
s.note((25,74),"Inputs via 1k protection (1% for CMRR). Low gain: electrode offset can reach +/-300 mV.",1.2)
s.note((25,77),"R9 is the AD623 gain resistor (RG on the datasheet); numbered R9 so KiCad annotation leaves it alone.",1.2)
J1 = s.sym("Connector","Conn_01x03_Pin","J1","Electrodes",(28,B1),
           refpos=(23.5,89.5),valpos=(19.5,106.5),fp=FP_J1)
U1 = s.sym("Amplifier_Instrumentation","AD623ANZ","U1","AD623AN",(85,B1),
           refpos=(99,88.5),valpos=(99,91),fp=FP_DIP8)
R1 = s.series("R1","1k",52,92.38,fp=FP_R)
R2 = s.series("R2","1k",52,102.54,fp=FP_R)
RG = s.sym("Device","R","R9","5.1k",(66,B1),0,refpos=(58.7,95.7),valpos=(58.7,98.3),fp=FP_R)
s.wire(J1["1"],(40,94.92),(40,92.38),R1["1"]); s.junction((40,92.38))
s.wire(R1["2"],U1["3"])
s.wire(J1["2"],(37,B1),(37,102.54),R2["1"]); s.junction((37,102.54))
s.wire(R2["2"],U1["2"])
lab_stub((40,92.38),"EL_P",-6); lab_stub((37,102.54),"EL_N",-6)
# reference electrode = J1.3, driven by the DRL amp (net DRL_REF). Drawn here at
# J1 so it travels with STAGE 1; the DRL block picks it up by net name.
s.wire(J1["3"],(33,B1),(33,113)); s.label((33,113),"DRL_REF",90,"left")
s.wire(U1["1"],(66,94.92),RG["1"]); s.wire(U1["8"],(66,100.0),RG["2"])
s.wire(U1["7"],(85,86.5)); s.power("+9V",(85,86.5))
s.wire(U1["4"],(85,109)); s.power("-9V",(85,109),down=True)
s.wire(U1["5"],(93,105.08),(93,111)); s.power("GND",(93,111))
lab_stub(U1["6"],"INA_OUT",8)
C1 = s.shunt("C1","100n",118,90,kind="C",fp=FP_C)
s.wire(C1["1"],(118,83.5)); s.power("+9V",(118,83.5)); s.wire(C1["2"],(118,97)); s.power("GND",(118,97))
C2 = s.shunt("C2","100n",131,90,kind="C",fp=FP_C)
s.wire(C2["1"],(131,83.5)); s.power("-9V",(131,83.5)); s.wire(C2["2"],(131,97)); s.power("GND",(131,97))
s.note((112,80),"AD623 decoupling",1.2)
s.box(17,64,135,116)

# ---------------------------------------------------------------- DRL
s.at("drl")
T(25,108,"DRIVEN-RIGHT-LEG  -  active mains-hum cancel")
s.note((25,155),"Averages EL_P/EL_N, inverts, and drives the reference electrode. R13 = 3.9M is deliberate:",1.2)
s.note((25,158),"~31 dB rejection but clips at ~32 mV Vcm; 10M would clip at only ~12 mV. See drl_analysis.py.",1.2)
U2C = s.sym("Amplifier_Operational","MCP6004","U2","MCP6004",(92,122),unit=3,
            refpos=(87.5,113),valpos=(87.5,115.5),fp=FP_DIP14)
sIN, pIN, oUT = U2C["9"], U2C["10"], U2C["8"]
# +IN -> VMID (up and away)
s.wire(pIN,(pIN[0],116),(78,116)); s.label((78,116),"VMID",180,"right")
# -IN = summing node DRL_S
lab_stub((sIN[0],sIN[1]),"DRL_S",-10)
# feedback Rf + Cf below the amp
s.wire(sIN,(sIN[0],133)); s.wire(oUT,(oUT[0],133))
R13 = s.series("R13","3.9M",92,133,fp=FP_R)
s.wire((sIN[0],133),R13["1"]); s.wire(R13["2"],(oUT[0],133))
s.junction((sIN[0],133)); s.junction((oUT[0],133))
s.wire((sIN[0],133),(sIN[0],139)); s.wire((oUT[0],133),(oUT[0],139))
C7 = s.series("C7","47p",92,139,kind="C",fp=FP_C)
s.wire((sIN[0],139),C7["1"]); s.wire(C7["2"],(oUT[0],139))
# output -> safety R -> reference electrode
lab_stub(oUT,"DRL_OUT",9)
R14 = s.series("R14","390k",120,122,fp=FP_R)
s.wire(oUT,R14["1"]); s.junction(oUT)   # op-amp output wired straight into R14 (one DRL_OUT label)
s.wire(R14["2"],(R14["2"][0]+6,122)); s.label((R14["2"][0]+6,122),"DRL_REF",0,"left")
# (the reference electrode J1.3 is labelled DRL_REF over in the STAGE 1 block)
# averaging resistors EL_P/EL_N -> DRL_S
R11 = s.series("R11","150k",58,144,fp=FP_R)
lab_stub(R11["1"],"EL_P",-6); lab_stub(R11["2"],"DRL_S",6)
R12 = s.series("R12","150k",58,150,fp=FP_R)
lab_stub(R12["1"],"EL_N",-6); lab_stub(R12["2"],"DRL_S",6)
s.note((25,161),"R14 = 390k limits body current to under 5 uA.",1.2)
s.box(17,104,133,166)

# ---------------------------------------------------------------- STAGE 2/3
s.at("stage23")
T(150,70,"STAGE 2/3  -  HPF -> x16 -> LPF 442 Hz")
C3 = s.series("C3","1u",156,B1,kind="C",fp=FP_C1U)
s.label((150,B1),"INA_OUT",180,"right"); s.wire((150,B1),C3["1"])
R3 = s.shunt("R3","8.2k",165,104.5,fp=FP_R)
s.wire(C3["2"],(173.62,B1)); s.junction((165,B1)); s.wire((165,B1),R3["1"])
s.wire(R3["2"],(165,112)); s.label((165,112),"VMID",270,"left")
U2A = s.sym("Amplifier_Operational","MCP6004","U2","MCP6004",(181.24,100),unit=1,
            refpos=(177,91),valpos=(177,93.5),fp=FP_DIP14)
s.wire((173.62,B1),U2A["3"])
s.wire(U2A["2"],(169.5,102.54),(169.5,112)); s.junction((169.5,112))
R4 = s.shunt("R4","1k",169.5,119,fp=FP_R)
s.wire((169.5,112),R4["1"]); s.wire(R4["2"],(169.5,126)); s.label((169.5,126),"VMID",270,"left")
R5 = s.series("R5","15k",178.5,112,fp=FP_R)
s.wire((169.5,112),R5["1"]); s.wire(R5["2"],(192.36,112),(192.36,100)); s.junction((192.36,100)); s.wire(U2A["1"],(192.36,100))
R6 = s.series("R6","3.6k",204,100,fp=FP_R)
s.wire((192.36,100),R6["1"])
C4 = s.shunt("C4","100n",214,106.5,kind="C",fp=FP_C)
s.wire(R6["2"],(221,100)); s.junction((214,100)); s.wire((214,100),C4["1"])
s.wire(C4["2"],(214,114)); s.power("GND",(214,114)); s.label((221,100),"LP_OUT")
s.box(143,63,232,135)

# ---------------------------------------------------------------- VMID buffer (U2B)
s.at("vmid")
T(118,150,"VMID 1.65 V REF  +  MCP6004 SUPPLY")
s.box(114,145,219,193)
R7 = s.shunt("R7","10k",160,164,fp=FP_R)
s.wire(R7["1"],(160,156.5)); s.power("+3V3",(160,156.5))
R8 = s.shunt("R8","10k",160,180,fp=FP_R)
s.wire(R7["2"],R8["1"]); s.junction((160,172)); s.wire(R8["2"],(160,187.5)); s.power("GND",(160,187.5))
C5 = s.shunt("C5","10u",172,177,kind="C",fp=FP_CEL)
s.wire((160,172),(172,172)); s.junction((160,172)); s.wire(C5["1"],(172,172)); s.junction((172,172))
s.wire(C5["2"],(172,184.5)); s.power("GND",(172,184.5))
U2B = s.sym("Amplifier_Operational","MCP6004","U2","MCP6004",(192,172),unit=2,
            refpos=(187.5,163),valpos=(187.5,165.5),fp=FP_DIP14)
s.wire((172,172),(184.38,172),(184.38,169.46)); s.wire(U2B["5"],(184.38,169.46))
s.wire(U2B["6"],(181,174.54),(181,182),(204,182),(204,172)); s.junction((204,172)); s.wire(U2B["7"],(204,172))
lab_stub((204,172),"VMID",8)

# ---------------------------------------------------------------- output buffer + ADC (U2D)
s.at("outbuf")
T(150,205,"OUTPUT BUFFER + ADC PROTECTION")
U2D = s.sym("Amplifier_Operational","MCP6004","U2","MCP6004",(180,215),unit=4,
            refpos=(175.5,206),valpos=(175.5,208.5),fp=FP_DIP14)
s.wire(U2D["12"],(170,212.46)); s.label((162,212.46),"LP_OUT",180,"right"); s.wire((162,212.46),(170,212.46))
s.wire(U2D["13"],(169,217.54),(169,224),(191,224),(191,215)); s.junction((191,215)); s.wire(U2D["14"],(191,215))
R10 = s.series("R10","1k",212,215,fp=FP_R)
s.wire((191,215),R10["1"])   # buffer output wired straight into R10 (no redundant net label)
s.wire(R10["2"],(232,215)); s.junction((226,215))
D1 = s.vdiode("D1","BAT85",226,208,fp=FP_D)
s.wire(D1["1"],(226,201)); s.power("+3V3",(226,201)); s.wire(D1["2"],(226,215))
D2 = s.vdiode("D2","BAT85",226,222,fp=FP_D)
s.wire(D2["1"],(226,215)); s.wire(D2["2"],(226,229)); s.power("GND",(226,229))
s.label((232,215),"ADC_IN"); s.note((198,231),"BAT85 clamps keep GP26 in-rail",1.15)
s.box(148,199,250,234)

# ---------------------------------------------------------------- MCP6004 power (unit 5)
s.at("mcp")
U2P = s.sym("Amplifier_Operational","MCP6004","U2","MCP6004",(140,172),unit=5,
            refpos=(146,169),valpos=(146,174),fp=FP_DIP14)
s.wire(U2P["4"],(U2P["4"][0],164)); s.power("+3V3",(U2P["4"][0],164))
s.wire(U2P["11"],(U2P["11"][0],180)); s.power("GND",(U2P["11"][0],180))
C6 = s.shunt("C6","100n",126,172,kind="C",fp=FP_C)
s.wire(C6["1"],(126,165)); s.power("+3V3",(126,165)); s.wire(C6["2"],(126,179)); s.power("GND",(126,179))
s.note((120,160),"One MCP6004 quad: 4 op-amps + rail decoupling.",1.15)

# ---------------------------------------------------------------- supply terminal
s.at("supply")
T(25,205,"SUPPLY  -  battery only (off-board)")
J4 = s.sym("Connector","Conn_01x03_Pin","J4","+9/GND/-9",(35,220),
           refpos=(30,211),valpos=(23,232),fp=FP_TERM)
lab_stub(J4["1"],"+9V",10); lab_stub(J4["2"],"GND",10); lab_stub(J4["3"],"-9V",10)
s.note((24,236),"Two 9V cells in series -> +9/GND/-9. Pico supplies +3V3. Star-ground at one point.",1.15)
s.box(18,201,118,240)

# ---------------------------------------------------------------- acquisition
s.at("acq")
T(250,150,"ACQUISITION  -  Pico / RP2040")
J2 = s.sym("Connector_Generic","Conn_02x20_Odd_Even","J2","Pico / RP2040",
           (275,190),refpos=(268,161),valpos=(264,221.5),fp="emg:Pico_Socket_2x1x20")
# Physical Pico pins: 31=GP26/ADC0, 36=3V3(OUT), 38=GND.
# GP0..GP4 sit on physical pins 1,2,4,5,6 and drive the five servo channels.
PICO_NETS = (("31","ADC_IN",None), ("36",None,"+3V3"), ("38",None,"GND"),
             ("1","SRV1",None), ("2","SRV2",None), ("4","SRV3",None),
             ("5","SRV4",None), ("6","SRV5",None))
for num,net,kind in PICO_NETS:
    p=J2[num]; right=p[0]>275; end=(p[0]+(12 if right else -12),p[1]); s.wire(p,end)
    (s.power(kind,end) if kind else s.label(end,net,0 if right else 180,"left" if right else "right"))
s.note((248,228),"GP26=ADC0. Sample at 1kHz on a hw timer; stream to Pi over USB.",1.2)
s.note((248,231),"GP0..GP4 (pins 1,2,4,5,6) = servo channels SRV1..SRV5.",1.2)
# Mark every Pico pin we deliberately leave open, so a real floating input
# is never hidden among 32 routine 'pin not connected' messages. (Stays in this
# block so the no-connects travel with J2.)
USED_PICO = {"1","2","4","5","6","31","36","38"}
for num in [str(k) for k in range(1,41)]:
    if num not in USED_PICO:
        s.nc(J2[num])
s.box(244,145,310,235)

# ---------------------------------------------------------------- servo output
s.at("servo")
T(245,248,"SERVO OUTPUT  -  isolated +5 V rail")
s.note((245,252),"Servo return NEVER shares the analog ground.",1.2)
s.note((245,255),"SERVO_GND meets GND only at R15 (star point).",1.2)

# +5 V servo supply entry (own screw terminal, off-board PSU)
FP_TERM2 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MPT-0,5-2-2.54_1x02_P2.54mm_Horizontal"
J10 = s.sym("Connector","Conn_01x02_Pin","J10","+5V servo in",(250,264),
            refpos=(244,258),valpos=(240,272),fp=FP_TERM2)
lab_stub(J10["1"],"+5VSRV",10); lab_stub(J10["2"],"SERVO_GND",10)

# bulk reservoir right at the supply entry - servo stall current would
# otherwise collapse the rail and brown out the Pico
C8 = s.shunt("C8","470u",282,268,kind="C",
             fp="Capacitor_THT:CP_Radial_D10.0mm_P5.00mm")
s.wire(C8["1"],(282,261)); s.label((282,261),"+5VSRV",90,"left")
s.wire(C8["2"],(282,275)); s.label((282,275),"SERVO_GND",270,"left")

# the single star-ground link between the servo return and analog ground
R15 = s.series("R15","0R",272,285,fp=FP_R)
s.wire(R15["1"],(R15["1"][0]-8,285)); s.label((R15["1"][0]-8,285),"SERVO_GND",180,"right")
s.wire(R15["2"],(R15["2"][0]+8,285)); s.power("GND",(R15["2"][0]+8,285))
s.note((245,290),"R15 = only SERVO_GND-GND link; fit 0R or wire.",1.15)
s.note((245,293),"Servo header order: GND / +5V / SIG.",1.15)

# five servo headers: pin1 GND, pin2 +5V, pin3 signal
FP_J3 = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
for i,(ref,y) in enumerate([("J5",250),("J6",260),("J7",270),("J8",280),("J9",290)],start=1):
    Jn = s.sym("Connector","Conn_01x03_Pin",ref,f"Servo {i}",(312,y),
               refpos=(306,y-6.5),valpos=(306,y+6.5),fp=FP_J3)
    lab_stub(Jn["1"],"SERVO_GND",9)
    lab_stub(Jn["2"],"+5VSRV",9)
    lab_stub(Jn["3"],f"SRV{i}",9)
s.box(240,244,340,300)

# ---------------------------------------------------------------- ERC hygiene
# Every rail arrives from a connector, so nothing in the schematic "drives"
# them as far as ERC is concerned. PWR_FLAG tells ERC these are fed externally.
s.at("flags")
T(25,250,"POWER FLAGS  -  rails fed from J4 / J2 / J10")
for i,(rail,x) in enumerate((("+9V",35),("-9V",60),("GND",85),("+3V3",110))):
    pt=(x,262); s.power(rail,pt); s.flag(pt)
s.note((25,268),"Flags exist only to satisfy ERC. They carry no copper.",1.2)
s.box(17,245,118,272)

render(s, str(pathlib.Path(__file__).resolve().parent / "emg.kicad_sch"))
print("rev E schematic written")
