# EMG front end rev E: design constraints

Constraints that follow from *this* circuit. Values are taken from the
netlist (`kicad/emg_new.net`), which is the authority - not from comments.

## What the board is

A single-channel surface-EMG front end feeding a Raspberry Pi Pico, plus a
five-channel servo output. Battery powered, body connected, 100 × 100 mm,
2-layer, entirely through-hole.

Signal chain, in order:

| Stage | Parts | Function |
|---|---|---|
| Input protection | R1, R2 (1k) | series limiting into the in-amp |
| Instrumentation amp | U1 AD623AN, R9 5.1k | gain = 1 + 100k/5.1k = **20.61** |
| AC couple | C3 1µF, R3 8.2k to VMID | strips electrode offset (±300 mV) |
| Gain stage | U2A, R4 1k, R5 15k | gain = 1 + 15k/1k = **16** |
| Low pass | R6 3.6k, C4 100n | **fc = 442 Hz** |
| Buffer | U2D | unity, drives the ADC |
| ADC protection | R10 1k, D1/D2 BAT85 | clamps to +3V3 and GND |
| Reference | R7/R8 10k, C5 10µ, U2B | VMID = **1.65 V** |
| Driven right leg | R11/R12 150k, U2C, R13 3.9M, C7 47p, R14 390k | active CM rejection |

**Total gain = 20.61 × 16 = 329.7 (call it 330).** The schematic previously carried a stale
note reading "21 × 48 = 1000", which described a superseded R5 = 47k. It has
been corrected. Do not reintroduce it.

**ADC scaling.** 12-bit over 0–3.3 V. One count = 3.3/4096 = 805.7 µV at the
pin; referred to the skin, divide by 329.7 → **2.443 µV per count**. VMID at
1.65 V is 2048 counts = 0 µV.

## Grounding

Two supply domains meet on this board and the rule is that they meet **once**.

- Analog runs on **±9 V** from J4, an off-board battery pair. U1's Ref pin
  ties to GND, so the in-amp output swings about 0 V.
- Digital runs on **+3V3 taken from the Pico** (J2 pin 36). U2 is powered
  from this rail, which is why VMID sits at 1.65 V rather than 0 V.
- Servo runs on a **separate +5 V** supply entering at J10, with C8 (470 µF)
  as the bulk reservoir right at the entry.

`SERVO_GND` is a **distinct net** from `GND`. It is poured over the bottom
strip of the board (y 82–94 mm) at zone priority 1, so it owns that copper
in preference to the board-wide GND pour at priority 0. **R15 (0R) is the
only connection between the two**, and it is placed deliberately straddling
the pour boundary - its SERVO_GND pad inside the strip, its GND pad outside.

The reason is current, not neatness: five servos stalling draw amps, and
that return current must not share a path with a front end resolving 2.44 µV
per count. Anything that shorts the two pours elsewhere - a stray track, a
misplaced pour, a solder bridge - silently destroys the noise floor while
leaving the board apparently functional. Stage 0 of `bringup.md` checks for
exactly this by lifting R15 and confirming the nets go open.

## High-impedance nodes

Three nodes are high enough impedance to care about physically:

- **EL_P / EL_N** (J1 pins 1, 2) - the electrode inputs. Source impedance is
  skin, typically 100 kΩ and mismatched between electrodes. Any leakage or
  coupling here appears directly at the input, multiplied by 330.
- **DRL_S** - the DRL summing node at U2C's inverting input, fed from
  R11/R12 (150k) and wrapped by R13 (**3.9 MΩ**). At megohms, this node picks
  up anything routed near it.
- **DRL_REF** (J1 pin 3) - driven, but through R14 (390 kΩ), so it is high
  impedance on the electrode side.

Constraints that follow:

- Keep all three short, and keep the DRL cluster physically next to J1.
  R11, R12 and R14 all connect to J1 and are placed adjacent to it for this
  reason; R13 and C7 only wrap U2C and sit slightly further out.
- **No servo net may run near these nodes.** SRV1–SRV5 are PWM edges; the
  layout keeps them on the opposite side of the board, leaving the Pico's
  outer pad column and running down the left channel to the header row.
- The board is poured on both layers, which guards these nodes; do not
  remove the pours to "simplify" routing.

## Input protection and clamping

D1 and D2 (BAT85 Schottky) clamp ADC_IN to +3V3 and GND respectively, fed
through R10 (1k). They must sit **at the Pico's ADC pin**, not back at the
buffer - the point is to protect GP26, so the clamp has to be electrically
closer to the pin than any pickup. R10 limits the clamp current.

D1 cathode to +3V3, D2 cathode to the signal. Reversed, they conduct in
normal operation and destroy the signal. Checked in bring-up stage 0.

## Bandwidth and aliasing

The analog low pass is R6/C4 at **442 Hz**, single pole. The Pico samples
ADC0 at **1 kHz**, so Nyquist is 500 Hz.

A single pole at 442 Hz is only about 7 dB down at 1 kHz, so content just
above Nyquist is **not** strongly suppressed. This is a deliberate accepted
compromise: surface EMG has little energy above 500 Hz, and the dominant
interferer (50 Hz mains) is far below Nyquist and handled by the DRL. If a
future revision raises the sample rate or needs a cleaner alias margin, add
a second pole rather than moving this one down - moving it down eats into
the 20–450 Hz EMG band.

## Decoupling

- C1 (100n) and C2 (100n) decouple U1's +9 V and −9 V rails; both are placed
  adjacent to U1.
- C6 (100n) decouples U2's +3V3 rail, adjacent to U2.
- C5 (10 µF) stabilises VMID at the R7/R8 divider.
- C8 (470 µF) is bulk reservoir for the servo rail at J10 - sized for servo
  inrush, not for decoupling. Without it a stall collapses the 5 V rail.

## Trace widths and current

| Net class | Width | Carries |
|---|---|---|
| Signal | 0.3 mm | µA; width set by manufacturability, not current |
| +9V, −9V, +3V3 | 0.8 mm | a few mA |
| +5VSRV, SERVO_GND | 1.5 mm (pour for the return) | amps under servo stall |

Vias are 0.8 mm outer / 0.4 mm drill.

## Body-connected device

This board drives current into a person through DRL_REF. R14 (390 kΩ) limits
that to **under 5 µA**, which is the standard approach and is safe - but the
safety argument depends entirely on the board being **battery powered and
galvanically isolated from mains**.

Consequences:

- The silkscreen carries "BATTERY POWER ONLY - no mains while worn".
- J4 is a screw terminal for off-board batteries; there is deliberately no
  provision for a mains adapter.
- The Pico's USB is the one path that can reach mains, via a charging
  laptop. `bringup.md` stages 4–6 require the laptop to be unplugged.
- Do not add a mains-powered peripheral, a wall-wart barrel jack, or a
  USB hub with its own supply to this design without re-doing the isolation
  analysis.

## Known open items

- **R13 = 3.9 MΩ is deliberate.** `REV_D_NOTES.md` claims ~19 dB CMRR from a
  simulation that used 10 MΩ. Nodal analysis (`kicad/drl_analysis.py`) gives
  ~31 dB at 3.9 MΩ and ~38 dB at 10 MΩ - but 10 MΩ has a correction gain of
  133×, clipping U2C at only ~12 mV of common-mode, versus 52× and ~32 mV
  for 3.9 MΩ. A clipped DRL rejects nothing, so 3.9 MΩ is the better choice
  in a realistic mains environment. Measured in bring-up stage 3.
- Absolute CMRR figures above assume 100 kΩ / 102 kΩ skin impedance and 5 pF
  mains coupling. Real values vary widely; the **relative** comparison
  between 3.9 M and 10 M is the robust part.
- The layout was produced programmatically (`place_board.py`,
  `route_board.py`), not by hand. It is DRC-clean, but no one has yet
  confirmed that it performs well at 2.44 µV per count. Stage 4 of bring-up
  is where that gets decided.
