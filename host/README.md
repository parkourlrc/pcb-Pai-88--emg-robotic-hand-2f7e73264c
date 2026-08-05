# Host observer

The control loop runs on the Pico. This is an observer: it plots, calibrates
and logs. **Unplugging it does not stop the gripper** — nothing here is in
the safety path.

Its job is to fill in the blanks in `board/revd/bringup.md`.

```bash
python3 -m host.cli selftest                 # no hardware needed
python3 -m host.cli monitor --seconds 60     # stage 4
python3 -m host.cli calibrate                # stage 5
python3 -m host.cli record out.npz --seconds 30
```

Add `--simulate` to any command to run against generated data with no Pico
attached. That is how the pipeline was verified here — it is not a
substitute for a bench test.

| Module | Role |
|---|---|
| `protocol.py` | PICO1 decode, CRC, resyncing stream parser |
| `transport.py` | serial (lazy pyserial), loopback, EMG simulator |
| `metrics.py` | stage-4 link stats, stage-5 calibration, gates |
| `cli.py` | the four subcommands above |

`protocol.py` is an independent implementation of
`firmware/pico_emg/include/protocol.h`. `tests/test_host.py` compiles the C
encoder and decodes its bytes here, then re-encodes and compares — because
a wire-format mismatch produces a link that looks alive and carries
nothing.
