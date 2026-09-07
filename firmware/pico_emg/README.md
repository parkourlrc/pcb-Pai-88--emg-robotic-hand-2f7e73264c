# EMG gripper firmware (Raspberry Pi Pico)

> **BATTERY POWER ONLY.** Never wear the electrodes while anything connected
> to the board touches mains. The driven-right-leg circuit actively drives
> your reference electrode. R14 (390k) holds body current under 5 µA, but
> that argument depends on the board being galvanically isolated from mains,
> and the Pico's USB is the one path that can reach it. The host must run on
> its own battery whenever electrodes are on skin.

Firmware for the rev E board: single-channel sEMG in, proportional gripper
out. **The entire control loop runs on the Pico.** The host plots,
calibrates and logs - unplug it and the gripper keeps working, because the
thing deciding whether a motor may move should not depend on a USB cable.

## Status

| | |
|---|---|
| Builds for RP2040 | **yes** - `pio run -e pico`, produces `firmware.uf2` |
| Logic tested | **yes** - 77 native tests, `./test/run_tests.sh` |
| Run on hardware | **no** - no board has been fabricated |

Nothing here has been executed on a Pico or seen a real electrode. The
tests verify the logic; they cannot verify the wiring, the ADC, the servo
timing or anything electrical. `board/revd/bringup.md` is what decides
whether this works.

## Why there is no classifier

The rev E board wires **one** EMG channel. Gen 1 had two (flexors and
extensors), which is what made a five-gesture classifier plausible. One
differential pair tells you reliably how hard a muscle is working, not
which of five postures a hand is in.

So contraction strength maps continuously onto grip aperture. That uses the
information the channel actually carries, and it removes a training set, a
model file and a whole runtime dependency. A gripper needs exactly one
degree of freedom, so the actuator and the sensor match.

## Signal path

```
GP26/ADC0 ──► 1 kHz hardware-timer ISR ──► ring buffer (512)
                                              │
     20 Hz high-pass ◄────────────────────────┘
     50/100/150 Hz notches
     rectify + 200 ms moving RMS  ──► envelope
                                        │
     safety state machine ◄─────────────┤ (quality: saturation,
                                        │  flatline, DC drift, staleness)
     proportional map + slew limit ◄────┘
                │
     GP0 servo PWM ──► J5 ──► gripper
```

There is deliberately **no digital low-pass**: the analog front end already
rolls off at 442 Hz and we sample at 1 kHz, so one near 450 Hz would sit on
top of Nyquist and buy nothing.

Measured response of the digital chain (native tests assert these):

| Frequency | Response |
|---|---|
| 5 Hz (motion artefact) | −24 dB |
| 20 Hz | −3 dB |
| 50 Hz (mains) | −25 dB |
| 75 Hz | flat |
| 100 / 150 Hz (harmonics) | −37 / −40 dB |
| 200–450 Hz | flat |

## Safety states

`INIT → IDLE → ACTIVE`, with `HOLD`, `FAULT` and a latched `ESTOP`.

- **HOLD** - recoverable doubt. Freezes the aperture *bit-exactly*. Needs
  5 consecutive good windows to leave, so a marginal signal cannot chatter
  the gripper open and shut.
- **FAULT** - stale stream or sustained bad signal. Releases the grip,
  slew-limited. Needs 20 consecutive good windows, then re-qualifies
  through IDLE with a **reset counter** - recovery genuinely costs
  20 + 10 good windows before anything moves.
- **ESTOP** - latches. 500 perfect windows will not clear it; only
  `safe_reset()` does.

There are no force sensors on rev E, so **signal quality is the primary
safety input**. One deliberate negative case: a quiet resting muscle must
not be read as a flatline, or the gripper faults every time you relax. The
flatline threshold sits well below any plausible resting level, and there
is a test for exactly that.

Releasing on fault rather than freezing is the one genuinely contestable
choice - see the comment above `grip_update()`. For a bench gripper,
releasing is safer: a frozen grip with no signal keeps crushing, and
nothing on this board can notice. Revisit if it ever holds something that
must not be dropped.

## Layout

```
include/config.h     pins, rates, thresholds, board constants
include/protocol.h   PICO1 frame + CRC-16/CCITT-FALSE
include/dsp.h        biquads + moving-RMS envelope
include/safety.h     safety state machine
include/control.h    envelope -> aperture, slew limiting
src/main.cpp         the wiring: ISR, ring buffer, servo, USB
test/test_native.c   77 tests over everything except main.cpp
```

Every header is plain C99, header-only, `static inline`, and compiles as
both C and C++. That is what lets the native tests exercise **the same
source** the Pico runs. `main.cpp` is the only file the tests cannot reach,
and it is deliberately kept to wiring so there is as little untested code
as possible.

## Build

```bash
pio run -e pico                       # builds firmware.uf2
pio run -e pico -t upload             # hold BOOTSEL, then upload
./test/run_tests.sh                   # 77 native logic tests
```

`pico_debug` is the same firmware plus a once-a-second status line on USB -
useful during bring-up stage 4, before any host tooling exists.

Footprint: **4.0% RAM, 2.0% flash**. The loop is nowhere near the limit;
the 1 kHz ISR does a single ADC read and a ring-buffer write, nothing else.

## Telemetry (PICO1)

54-byte frames, 20 samples each, 50 frames/s. Little-endian.

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | magic `0xE2 0xA2` |
| 2 | 1 | version = 1 |
| 3 | 1 | flags |
| 4 | 2 | seq (wraps) |
| 6 | 4 | t_us of the first sample |
| 10 | 1 | n = 20 |
| 11 | 1 | reserved |
| 12 | 40 | samples, raw 12-bit ADC |
| 52 | 2 | CRC-16/CCITT-FALSE |

Flags: `0x01` saturated high, `0x02` saturated low, `0x04` contact
suspect, `0x08` overrun (USB backed up; samples dropped deliberately
rather than stalling the control loop).

Outbound is telemetry only. Inbound carries calibration and commands -
never the control loop.

## Calibration

`grip_calibrate(cfg, rest, max)` takes two measured envelope levels: relaxed,
and a comfortable hard squeeze. It **rejects** an inverted or implausibly
small span rather than installing a divide-by-zero, and leaves the previous
calibration intact when it does.

The defaults in `grip_config_defaults()` are placeholders. Shipping a guess
would make the gripper twitch at noise on one arm and not respond at all on
another - calibrate during bring-up stage 5.

## One ADC count

`(3.3 / 4096) / 329.7 = 2.443 µV` at the skin, where 329.7 is the real
total gain (AD623 at 20.61 × U2A at 16). The schematic's old "21 × 48 =
1000" note was stale and has been corrected - do not reintroduce it.
