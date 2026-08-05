# EMG rev D — schematic (verified) + driven-right-leg

## What's improved over rev C
- **Driven-right-leg (DRL) circuit** — the real fix for 50 Hz mains hum.
  Simulated here: loop is STABLE and adds ~19 dB common-mode rejection on
  top of the AD623's own CMRR. Averages the two inputs (R11/R12 150k),
  inverts via U2C, drives the reference electrode through R14 390k
  (limits body current to <5 uA). Cf=47p across R13 ensures HF stability.
- **Quad op-amp (MCP6004, DIP-14)** replaces the dual — now runs 4 roles:
  U2A gain, U2B VMID buffer, U2C DRL, U2D output buffer (drives the ADC).
- **Sensible default gain** — R5 = 15k (total ~336), no clipping to ~5 mV.
- **1% input resistors** (R1/R2) for better CMRR.
- **Off-board batteries** via a 3-pin screw terminal (J4) — no bulky holders.
- Redundant Pi header removed (ground reaches the Pi through the Pico USB).

## Verification done here
- Netlist: every net checked; no shorts, no floating pins; 4 op-amp outputs
  on 4 separate correct nets; DRL and VMID nets correctly separated.
- DRL loop simulated in ngspice: stable + effective (values above are the
  simulated-good ones, now on the schematic).

## Status of the PCB layout
NOT done. A compact, good-looking layout is interactive craft in KiCad and
is deliberately left for that. The schematic here is complete and correct;
open emg.kicad_sch, then place + route in the PCB editor (that's also the
best learning). Ask if you want a placement guide.

## Parts delta vs rev C shopping list
ADD:  MCP6004-I/P (DIP-14) x1;  R11,R12 150k;  R13 3.9M;  R14 390k;
      C7 47pF;  3-pin 5.08mm screw terminal (J4);  14-pin DIP socket.
DROP: MCP6002;  the two PP3 battery holders (use off-board clips to J4).
KEEP: everything else from the rev C list. R5 now 15k (not 47k).

## Safety
Battery only. The DRL now actively drives your reference electrode — R14
keeps the current to microamps, which is standard and safe, but this is
another reason to NEVER connect while anything touches mains.
