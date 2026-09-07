# EMG front end rev E: fabrication

Fab house: **JLCPCB**.
DRC rules: `~/hw/drc/JLCPCB.kicad_dru`, copied into this project as
`kicad/emg.kicad_dru`.

## Board specification

| Parameter | Value |
|---|---|
| Size | **100 × 100 mm**, 5 mm chamfered corners |
| Layers | 2 (F.Cu, B.Cu) |
| Material | FR4, 1.6 mm |
| Copper | 1 oz outer |
| Surface finish | HASL lead-free is fine; ENIG if you want flatter pads |
| Solder mask | any colour |
| Silkscreen | white (or black on light mask) |
| Min track / clearance used | 0.3 mm / 0.25 mm |
| Min drill used | 0.4 mm (vias) |
| Via | 0.8 mm outer / 0.4 mm drill |
| Mounting | 4 × M3 (3.2 mm) at the corners |

100 × 100 mm is the boundary of JLCPCB's cheapest tier - staying at or under
it is why the board was shrunk from the original 150 × 125 mm.

## Rule margins

From `JLCPCB.kicad_dru`, against what the board actually uses:

| Rule | JLCPCB minimum | This board | Margin |
|---|---|---|---|
| Track width | 0.127 mm | 0.3 mm | 2.4× |
| Copper clearance | 0.127 mm | 0.25 mm | 2.0× |
| Via drill | 0.3 mm | 0.4 mm | 1.3× |
| Via diameter | 0.5 mm | 0.8 mm | 1.6× |
| PTH drill | 0.3 mm | 0.8 mm smallest | 2.7× |
| Hole to hole | 0.5 mm | ≥0.5 mm enforced by the router | at limit |
| Copper to edge | 0.3 mm | 0.7 mm keep-out | 2.3× |
| Silk text height | 1.0 mm | 1.0 mm smallest | at limit |

Everything is comfortably inside capability. Nothing here needs a special
process, controlled impedance, or tighter-than-standard tolerances.

## Assembly

**This board is entirely through-hole. JLCPCB assembly does not apply - do
not order PCBA.** Order bare boards only and hand-solder.

`release_*/cpl.csv` is produced by the export script but is not useful to a
THT assembler; ignore it unless you switch to SMT in a future revision.

Suggested hand-assembly order (shortest parts first, so the board sits flat):

1. Resistors R1–R15 (R9 is the AD623 gain resistor), then the diodes D1, D2 (watch polarity - D1
   cathode to +3V3, D2 cathode to the signal).
2. DIP **sockets** for U1 (8-pin) and U2 (14-pin). Use sockets: bring-up
   stage 1 requires powering the board with the ICs *out*.
3. Ceramic capacitors C1, C2, C4, C6, C7, then C3.
4. Electrolytics C5 (10 µF) and C8 (470 µF) - polarity marked on silk.
5. Headers J1, J5–J9 and the Pico socket J2.
6. Screw terminals J4 and J10.
7. R15 last, so you can leave it out while checking pour isolation.

## Pre-submission checklist

- [ ] DRC run with `emg.kicad_dru` loaded: **0 errors**.
      (227 warnings are expected and triaged - see below.)
- [ ] Schematic parity: only the 4 mounting holes reported as extra, which
      is by design since they are board-only.
- [ ] All 59 nets routed, 0 unconnected items.
- [ ] Board outline is a single closed loop on Edge.Cuts.
- [ ] Gerber zip opens in a viewer and shows the expected 8 layers + drill.
- [ ] Silkscreen reads correctly and the "BATTERY POWER ONLY" text is legible.
- [ ] Confirm you are ordering **bare boards, not assembled**.
- [ ] Confirm 2 layers, 1.6 mm, 1 oz - not 4-layer by accident.

### Expected DRC warnings, and why they are not defects

The board reports **0 errors** and 227 warnings. All 227 were triaged:

- **199 × `silk_overlap`.** 148 of these are silkscreen printed over the
  ground pour. That is normal on any poured board - the pour is under solder
  mask, so silk over it is exactly what you want. The remaining ~44 are a
  component's own reference text touching its own outline. Neither affects
  fabrication.
- **27 × `lib_footprint_mismatch`.** The board's footprints came from a
  KiCad 7 library and the installed libraries are KiCad 10. All 40 footprints
  were compared against the installed library programmatically:
  **pad names, positions, sizes and drill diameters are identical.** What
  differs is the pad *shape style* - the board draws pin 1 as a rectangle
  where the library uses a rounded rectangle, and the remaining pads as
  ovals where the library uses circles. Since the oval axes are equal
  (1.6 x 1.6 mm) those are geometrically the same aperture, and the corner
  radius on pin 1 has no electrical or mechanical consequence. Plus a
  differing count of drawing items on non-copper layers. Zero fab impact,
  so left alone rather than refreshing footprints and disturbing a board
  that currently passes DRC cleanly.
- **1 × `silk_over_copper`.** One reference field clipped by solder mask.
  Cosmetic.

## Files to upload

From `kicad/release_<date>/`:

| File | Purpose |
|---|---|
| `emg-revE_gerbers.zip` | **the only file JLCPCB needs** - 8 layers + drill |
| `bom.csv` | your shopping list, not for JLCPCB (THT, hand-assembled) |
| `top.pdf`, `top.svg`, `bottom.svg` | visual check before ordering |
| `board_preview.png` | quick look at both copper layers together |
| `drc_parity.rpt` | the DRC/parity report backing the checklist above |

Regenerate the whole set with:

```bash
cd ~/Documents/emg_hand/board/revd/kicad && PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH" ~/hw/scripts/build_release.sh emg-revE
```

## After the boards arrive

Do not describe this design as working on the strength of a successful
fabrication. Go to `bringup.md` and work through every gate.
