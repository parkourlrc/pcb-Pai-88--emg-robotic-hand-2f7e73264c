# EMG front end rev E: bring-up

> Nothing in this project may be described as working until every stage
> below passes on real hardware. Where things stand (20 Aug 2026):
>
> - Board fabricated at JLCPCB and hand-soldered. The servo section (J5-J9,
>   J10, C8) is not populated. C3 is not fitted yet.
> - U1 and U2 were soldered directly, without DIP sockets. Stage 1 as written
>   (rails with the ICs out) therefore cannot be done on this board. Skip to
>   stage 2 and check the rails with the ICs in, at low current, with a
>   current-limited supply if you have one.
> - Fitted values that differ from the schematic: R5 = 10k (not 15k),
>   R6 = 3.3k, R11 = R12 = 100k, R13 = 3.3M, R14 = 470k. So expect a total
>   gain near 227 in stage 3, not 330.
> - Stage 0 continuity checks done: +9/GND, -9/GND and +3V3/GND all open.
> - The +/-9 V rails have not been connected yet, so stages 1 to 3 are not
>   done and nothing on the analog side is verified.
> - Stage 4 link test done with the Pico on USB and the analog side
>   unpowered: 10 s capture, 499 frames, 49.87 frames/s, 997.4 Hz, 0 CRC
>   errors, 0 dropped, 0 resyncs. The quiet-input rows in stage 4 do not
>   count from that run because the input was floating.
> - Stages 5 and 6 not started.

**BATTERY POWER ONLY. Never wear the electrodes while anything connected to
the board touches mains.** The driven-right-leg circuit actively drives your
reference electrode. R14 (390k) limits body current to under 5 µA, which is
standard practice, but plugging the Pico into a mains-powered laptop while
electrodes are on skin defeats the battery isolation that makes this safe.
During stages 5 and 6 the laptop must be unplugged and running on its own
battery, or the Pico must be powered from a battery bank.

Work top to bottom. Each stage has a gate. **Do not proceed past a failed
gate** - later stages assume the earlier ones passed, and several of them
put voltage near your skin.

---

## Stage 0: Inspection, before any power

No power supply connected. Nothing seated in the DIP sockets.

- [ ] Visual: no solder bridges, especially between the DIP-14 pins and
      between the Pico socket rows.
- [ ] Confirm **R9 = 5.1k** is fitted. This is the AD623 gain resistor
      (called RG on the datasheet; numbered R9 here so KiCad annotation
      leaves it alone). A missing R9 gives gain 1, not 20.6.
- [ ] Confirm **R5 = 15k**, not 47k. R5 sets stage-2 gain; 15k gives the
      intended total of 330. A 47k here gives ~990 and will clip.
- [ ] Confirm **R15 (0R link) is fitted.** This is the only connection
      between SERVO_GND and analog GND. Without it the servo ground floats.
- [ ] Continuity: SERVO_GND to GND should read ~0 Ω **through R15 only**.
      Lift one end of R15 and re-measure: it must now read open. If it still
      reads short, the two pours are touching somewhere and the star ground
      is defeated.  Measured with R15 lifted: ___________ Ω  (expect: open)
- [ ] Continuity: no short between +9V and GND, −9V and GND, or +3V3 and GND.
      +9/GND ______ Ω   −9/GND ______ Ω   +3V3/GND ______ Ω  (expect: open)
- [ ] Check D1 and D2 orientation. D1 cathode goes to +3V3, D2 cathode goes
      to the ADC_IN signal. Reversed clamps will conduct in normal operation.

**Gate 0:** no shorts, R5/R9/R15 correct, clamp diodes the right way round.

---

## Stage 1: Rails only, no ICs

Leave U1 and U2 **out of their sockets**. Do not fit the Pico.
Connect the ±9 V battery supply to J4 (+9 / GND / −9, left to right).

- [ ] +9 V rail at U1 pin 7 socket:      ________ V   (expect +8.5 to +9.5)
- [ ] −9 V rail at U1 pin 4 socket:      ________ V   (expect −8.5 to −9.5)
- [ ] Total current draw from the battery: ________ mA (expect < 1 mA;
      nothing is loading the rails yet)

Now fit the Pico and power it over USB **from a battery-powered laptop or a
battery bank**, still with no ICs seated.

- [ ] +3V3 at U2 pin 4 socket:           ________ V   (expect 3.2 to 3.4)

**Gate 1:** all three rails in range, negligible current. A rail that is
low or a current draw of tens of mA means a short - stop and find it.

---

## Stage 2: ICs seated, nothing connected to the body

Power off. Seat U1 (AD623AN, DIP-8) and U2 (MCP6004, DIP-14), both pin 1
toward the silkscreen marker. **Nothing in J1. No servo supply on J10.**
Power up again.

- [ ] Current draw now:                  ________ mA  (expect a few mA)
- [ ] VMID (U2 pin 7, the U2B output):   ________ V   (expect 1.60 to 1.70;
      this is R7/R8 dividing +3V3, so it should be almost exactly half)
- [ ] U2 pin 1 (U2A output):             ________ V   (expect near VMID)
- [ ] U2 pin 8 (U2C output, the DRL):    ________ V   (expect near VMID)
- [ ] U2 pin 14 (U2D output, the buffer):________ V   (expect near VMID)
- [ ] U1 pin 6 (AD623 output):           ________ V   (expect within a few
      hundred mV of 0 V - its Ref pin is tied to GND, not VMID)
- [ ] ADC_IN at the Pico side of R10:    ________ V   (expect near VMID,
      and it must stay between 0 and 3.3 V or the clamps are conducting)

**Gate 2:** VMID is 1.65 V and every op-amp output sits at a sane DC level.
An output pinned at a rail means that stage is saturated - find out why
before going near a body.

---

## Stage 3: Bench signal injection, still no body connection

Use a function generator with its output floating or heavily attenuated.
Drive between EL_P and EL_N (J1 pins 1 and 2). Keep the amplitude tiny:
the total gain is 330, so 5 mV in gives 1.65 V out and is right at the rail.

**Differential gain.** Inject 1 mVpp at 100 Hz.

- [ ] Output at BUF (U2 pin 14), pk-pk:  ________ mV
- [ ] Computed gain = output / input:    ________     (**expect ~330**,
      i.e. 20.61 from the AD623 times 16 from U2A; anywhere in 310-350 is
      fine once resistor tolerance is allowed for)

If you measure roughly 1000, R5 is a 47k left over from the old design.
The number 330 is the correct one; the "21 x 48 = 1000" note that used to
be on the schematic was stale and has been removed.

**Bandwidth.** Hold the input at 1 mVpp and sweep.

- [ ] Gain at 10 Hz:    ________   (expect rolled off; C3 high-passes)
- [ ] Gain at 100 Hz:   ________   (expect flat, ~330)
- [ ] Gain at 442 Hz:   ________   (**expect -3 dB, i.e. ~233**, set by
      R6 3.6k with C4 100n)
- [ ] Gain at 1 kHz:    ________   (expect well down)

**Common-mode rejection - measure it, do not assume it.** Tie EL_P and
EL_N together through 100k each and drive that common point with 100 mVpp
at 50 Hz, with DRL_REF (J1 pin 3) returned through a 100k resistor to the
same common point to stand in for skin.

- [ ] Output with the DRL loop intact:      ________ mV pk-pk
- [ ] Output with R13 lifted (DRL disabled):________ mV pk-pk
- [ ] Improvement = 20·log10(without / with): ________ dB

**Expect roughly 30 dB.** Nodal analysis of this network
(`kicad/drl_analysis.py`) gives about 31 dB at the fitted R13 = 3.9M.

Note on R13, so you do not "fix" a deliberate choice: `REV_D_NOTES.md`
claimed ~19 dB from a simulation that used a 10 M feedback resistor, but the
board ships 3.9 M. That is intentional. 10 M gives about 7 dB more rejection
but its correction gain of 133× means U2C clips at only ~12 mV of body
common-mode; 3.9 M has a gain of 52× and survives to ~32 mV. Mains
common-mode on a floating body is routinely tens of mV, and **a clipped DRL
rejects nothing at all**. If your measurement comes in far below 30 dB,
check whether U2C is clipping before you reach for a bigger resistor.

**Gate 3:** gain ~330, corner ~442 Hz, CMRR improvement ≈ 30 dB, and
U2C not clipping.

---

## Stage 4: Acquisition, battery only

Flash the Pico. **The laptop must now be unplugged from mains** (running on
its own battery) or the Pico must run from a battery bank.

- [ ] Sample rate measured at the host:  ________ Hz   (expect 1000 ± 1)
- [ ] Frame rate:                        ________ /s   (expect 50)
- [ ] CRC error rate over 60 s:          ________      (expect 0)
- [ ] Dropped frames over 60 s:          ________      (expect 0)
- [ ] Quiet-input ADC reading, mean:     ________ counts (expect ~2048)
- [ ] Quiet-input noise, pk-pk:          ________ counts
      (1 count = 2.443 µV at the skin, so this is your noise floor)

**Gate 4:** the link runs clean for a full minute with no CRC errors and the
baseline sits near mid-scale.

---

## Stage 5: On the body

Battery power only. Laptop unplugged from mains. If anything is plugged into
a wall socket anywhere in the signal path, **stop**.

- [ ] Electrodes placed on a forearm muscle, reference on a bony prominence.
- [ ] Resting baseline, pk-pk:           ________ counts
- [ ] 50 Hz content visible in the resting trace?  yes / no
      (if yes, check electrode contact and the DRL before blaming the board)
- [ ] Voluntary contraction, pk-pk:      ________ counts
- [ ] Contraction-to-rest ratio:         ________     (want a clear margin;
      this is what any classifier downstream depends on)

**Gate 5:** contraction is unambiguously distinguishable from rest, and the
resting trace is not dominated by mains.

---

## Stage 6: Servo subsystem, last

Only after stage 5 passes. Connect the separate +5 V servo supply to J10
(+5V / GND). **Do not** power the servos from the Pico.

- [ ] Servo supply voltage at J10:       ________ V   (expect 5.0 to 6.0)
- [ ] With hand mechanically unloaded, command one servo. Does it move
      smoothly and stop where asked?  yes / no
- [ ] Repeat for all five channels, SRV1 to SRV5 on J5 to J9.
- [ ] **The important one:** with the electrodes still on the body and a
      servo sweeping continuously, does the EMG baseline change?
      Resting pk-pk with servos idle:   ________ counts
      Resting pk-pk with servos moving: ________ counts
      These should be close. A large increase means servo return current is
      getting into the analog ground - check R15 and the pour boundary.
- [ ] Stall a servo briefly and confirm the +5 V rail does not sag enough to
      reset the Pico.  Rail under stall: ________ V

**Gate 6:** all five channels move, and servo activity does not corrupt the
EMG trace.

---

## Only now

When every gate above has passed with real numbers written in, the board may
be described as working - and only to the extent that these stages tested.
Record the completed copy of this document alongside the design files, and
note the date and the board serial.

Bring-up completed by: ______________  Date: __________  Board #: _______
