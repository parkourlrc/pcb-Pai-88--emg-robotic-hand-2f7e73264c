"""Host observer for the EMG gripper.

The control loop runs on the Pico. This tool watches it: it plots nothing
the Pico depends on, and unplugging it does not stop the gripper.

Its job is to fill in the blanks in board/revd/bringup.md.

    python3 -m host.cli selftest              # no hardware needed
    python3 -m host.cli monitor --seconds 60  # bring-up stage 4
    python3 -m host.cli calibrate             # bring-up stage 5
    python3 -m host.cli record out.npz --seconds 30
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from . import metrics, protocol, transport

BANNER = """\
+----------------------------------------------------------------+
|  BATTERY POWER ONLY - no mains while electrodes are worn.       |
|  Unplug the laptop from the wall, or run the Pico from a        |
|  battery bank, before putting electrodes on skin.               |
+----------------------------------------------------------------+"""


def _open(args):
    """Real hardware, or the simulator when --simulate is given."""
    if args.simulate:
        payload = transport.simulate_emg(
            int(args.seconds * protocol.FRAME_RATE_HZ),
            contracted=False, mains_counts=args.sim_mains)
        return transport.LoopbackTransport(payload, chunk=256), True
    print(BANNER)
    return transport.SerialTransport(port=args.port), False


def _collect(args, seconds, *, contracted=False):
    """Read for `seconds`, returning (parser, recorder, elapsed)."""
    if args.simulate:
        payload = transport.simulate_emg(
            int(seconds * protocol.FRAME_RATE_HZ), contracted=contracted,
            mains_counts=args.sim_mains, seed=1 if contracted else 0)
        src = transport.LoopbackTransport(payload, chunk=256)
    else:
        src = transport.SerialTransport(port=args.port)

    parser = protocol.FrameStreamParser()
    rec = metrics.Recorder()
    t0 = time.monotonic()
    with src as t:
        while True:
            elapsed = time.monotonic() - t0
            if not args.simulate and elapsed >= seconds:
                break
            chunk = t.read()
            if not chunk:
                if args.simulate:
                    break
                time.sleep(0.005)
                continue
            for f in parser.feed(chunk):
                rec.add(f)
    elapsed = seconds if args.simulate else (time.monotonic() - t0)
    return parser, rec, elapsed


def _report_link(parser, elapsed):
    st = metrics.LinkStats(frames_ok=parser.frames_ok,
                           crc_errors=parser.crc_errors,
                           dropped=parser.dropped,
                           resyncs=parser.resyncs,
                           elapsed_s=elapsed)
    print("\n-- link (bring-up stage 4) --------------------------------")
    print(f"  frames received      {st.frames_ok}")
    print(f"  frame rate           {st.frame_rate_hz:8.2f} /s     (expect 50)")
    print(f"  sample rate          {st.sample_rate_hz:8.1f} Hz    (expect 1000 +/- 1)")
    print(f"  CRC errors           {st.crc_errors:8d}        (expect 0)")
    print(f"  dropped frames       {st.dropped:8d}        (expect 0)")
    print(f"  resyncs              {st.resyncs:8d}")
    ok, why = st.gate_passes()
    print(f"  GATE 4               {'PASS' if ok else 'FAIL'}")
    for w in why:
        print(f"      - {w}")
    return st, ok


def _report_signal(rec, label="signal"):
    sig = rec.analyse()
    print(f"\n-- {label} ---------------------------------------------")
    print(f"  samples              {rec.array.size}")
    print(f"  mean                 {sig.mean_counts:8.1f} counts "
          f"(offset {sig.mean_offset_counts:+.1f}, expect ~0)")
    print(f"  pk-pk                {sig.pk_pk_counts:8.1f} counts "
          f"= {sig.pk_pk_uv:.1f} uV at the skin")
    print(f"  RMS                  {sig.rms_counts:8.2f} counts "
          f"= {sig.rms_uv:.2f} uV")
    print(f"  50 Hz share of power {sig.mains_ratio*100:8.1f} %")
    print(f"  saturated samples    {sig.saturated_fraction*100:8.2f} %")
    if sig.mains_ratio > 0.3:
        print("      ! mains dominates - check electrode contact and the DRL")
    if sig.saturated_fraction > 0.001:
        print("      ! clipping against the BAT85 clamps - reduce gain or check offset")
    return sig


def cmd_monitor(args):
    parser, rec, elapsed = _collect(args, args.seconds)
    _report_link(parser, elapsed)
    _report_signal(rec, "signal (stage 4 baseline)")
    print("\nCopy these into board/revd/bringup.md stage 4.")
    return 0


def cmd_calibrate(args):
    print(BANNER if not args.simulate else "(simulated)")
    print("\nStage 5 calibration. Two captures.\n")

    if not args.simulate:
        input("  1) RELAX completely, then press Enter... ")
    _, rest_rec, _ = _collect(args, args.seconds, contracted=False)
    rest = _report_signal(rest_rec, "resting")

    if not args.simulate:
        input("\n  2) SQUEEZE and hold comfortably, then press Enter... ")
    _, con_rec, _ = _collect(args, args.seconds, contracted=True)
    con = _report_signal(con_rec, "contracted")

    cal = metrics.Calibration(rest_rms=rest.rms_counts,
                              contract_rms=con.rms_counts)
    ok, why = cal.gate_passes()
    print("\n-- calibration --------------------------------------------")
    print(f"  rest RMS             {cal.rest_rms:8.2f} counts")
    print(f"  contract RMS         {cal.contract_rms:8.2f} counts")
    print(f"  ratio                {cal.ratio:8.1f} : 1   (want >= 3)")
    print(f"  GATE 5               {'PASS' if ok else 'FAIL'}")
    for w in why:
        print(f"      - {w}")
    cfg = cal.suggest_grip_config()
    print("\n  Put these in the firmware (control.h / grip_calibrate):")
    print(f"      rest_level = {cfg['rest_level']}f;")
    print(f"      max_level  = {cfg['max_level']}f;")
    return 0 if ok else 1


def cmd_record(args):
    parser, rec, elapsed = _collect(args, args.seconds)
    _report_link(parser, elapsed)
    sig = _report_signal(rec)
    np.savez(args.output, counts=rec.array,
             sample_rate=protocol.SAMPLE_RATE_HZ,
             uv_per_count=protocol.UV_PER_COUNT,
             rms_counts=sig.rms_counts)
    print(f"\nwrote {args.output} ({rec.array.size} samples)")
    return 0


def cmd_selftest(args):
    """Exercise the whole host pipeline with no hardware at all."""
    print("host self-test (no hardware)\n")
    failures = []

    def check(name, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}   {detail}")
        if not cond:
            failures.append(name)

    check("CRC known answer", protocol.crc16(b"123456789") == 0x29B1)

    payload = transport.simulate_emg(200, contracted=False)
    p = protocol.FrameStreamParser()
    rec = metrics.Recorder()
    for f in p.feed(payload):
        rec.add(f)
    check("simulated stream decodes", p.frames_ok == 200,
          f"{p.frames_ok}/200 frames")
    check("no CRC errors on a clean stream", p.crc_errors == 0)
    check("no dropped frames on a clean stream", p.dropped == 0)

    p2 = protocol.FrameStreamParser()
    list(p2.feed(b"\x01\x02garbage" + payload[:540]))
    check("recovers from leading garbage", p2.frames_ok > 0 and p2.resyncs > 0,
          f"{p2.frames_ok} frames, {p2.resyncs} resyncs")

    rest = metrics.analyse(rec.array)
    con_rec = metrics.Recorder()
    p3 = protocol.FrameStreamParser()
    for f in p3.feed(transport.simulate_emg(200, contracted=True, seed=1)):
        con_rec.add(f)
    con = metrics.analyse(con_rec.array)
    check("contraction separates from rest",
          con.rms_counts > 5 * rest.rms_counts,
          f"{con.rms_counts:.1f} vs {rest.rms_counts:.1f} counts")

    cal = metrics.Calibration(rest.rms_counts, con.rms_counts)
    ok, _ = cal.gate_passes()
    check("calibration gate accepts a good separation", ok,
          f"ratio {cal.ratio:.1f}")

    mains_rec = metrics.Recorder()
    p4 = protocol.FrameStreamParser()
    for f in p4.feed(transport.simulate_emg(200, mains_counts=80.0)):
        mains_rec.add(f)
    check("mains contamination is detected",
          metrics.analyse(mains_rec.array).mains_ratio > 0.3,
          f"{metrics.analyse(mains_rec.array).mains_ratio*100:.0f}% at 50 Hz")

    try:
        import serial  # noqa: F401
        port = transport.SerialTransport.autodetect_port()
        check("pyserial present", True, f"autodetect: {port or 'no RP2040 attached'}")
    except ImportError:
        check("pyserial present", False, "install it before bring-up stage 4")

    print(f"\n{'all checks passed' if not failures else str(len(failures)) + ' failed'}")
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="host.cli", description="EMG gripper host observer")
    ap.add_argument("--port", help="serial port (default: autodetect RP2040)")
    ap.add_argument("--simulate", action="store_true",
                    help="use generated data instead of hardware")
    ap.add_argument("--sim-mains", type=float, default=0.0,
                    help="counts of 50 Hz to inject when simulating")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("monitor", help="link and signal stats (stage 4)")
    m.add_argument("--seconds", type=float, default=60.0)
    m.set_defaults(func=cmd_monitor)

    c = sub.add_parser("calibrate", help="capture rest and contract (stage 5)")
    c.add_argument("--seconds", type=float, default=5.0)
    c.set_defaults(func=cmd_calibrate)

    r = sub.add_parser("record", help="capture raw samples to npz")
    r.add_argument("output")
    r.add_argument("--seconds", type=float, default=30.0)
    r.set_defaults(func=cmd_record)

    s = sub.add_parser("selftest", help="exercise the pipeline, no hardware")
    s.add_argument("--seconds", type=float, default=5.0)
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ImportError, RuntimeError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
