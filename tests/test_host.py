"""Host-side tests: protocol, stream parsing, metrics, transport.

Runs with numpy + scipy + pytest only. pyserial and torch are absent on
this machine and nothing here may require them.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from host import metrics, protocol, transport

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------- protocol
def test_crc_known_answer():
    assert protocol.crc16(b"123456789") == 0x29B1


def test_frame_size():
    assert protocol.frame_size(20) == 54
    assert protocol.frame_size(0) == 14


def test_round_trip_preserves_everything():
    samples = np.arange(2000, 2020, dtype=np.uint16)
    raw = protocol.encode_frame(0xBEEF, 0x12345678, samples,
                                flags=protocol.Flags.SATURATED_HIGH)
    assert len(raw) == 54
    f = protocol.decode_frame(raw)
    assert f.seq == 0xBEEF
    assert f.t_us == 0x12345678
    assert f.flags == protocol.Flags.SATURATED_HIGH
    assert f.saturated and not f.overrun
    assert np.array_equal(f.samples, samples)


@pytest.mark.parametrize("index,exc", [
    (0, protocol.BadMagic),
    (2, protocol.BadVersion),
])
def test_header_corruption_is_specific(index, exc):
    raw = bytearray(protocol.encode_frame(1, 2, np.zeros(20, np.uint16)))
    raw[index] ^= 0xFF
    with pytest.raises(exc):
        protocol.decode_frame(bytes(raw))


def test_truncated_frame():
    raw = protocol.encode_frame(1, 2, np.zeros(20, np.uint16))
    with pytest.raises(protocol.BadLength):
        protocol.decode_frame(raw[:30])


def test_every_payload_bit_flip_is_caught():
    raw = protocol.encode_frame(7, 9, np.arange(2000, 2020, dtype=np.uint16))
    for i in range(protocol.HEADER_SIZE, len(raw) - 2):
        for bit in range(8):
            bad = bytearray(raw)
            bad[i] ^= 1 << bit
            with pytest.raises(protocol.BadCRC):
                protocol.decode_frame(bytes(bad))


def test_seq_gap_survives_wrap():
    assert protocol.seq_gap(100, 101) == 0
    assert protocol.seq_gap(100, 105) == 4
    assert protocol.seq_gap(65535, 0) == 0        # the one that bites
    assert protocol.seq_gap(65534, 1) == 2


def test_microvolt_scaling():
    f = protocol.decode_frame(
        protocol.encode_frame(0, 0, np.array([2048, 2049], np.uint16)))
    uv = f.to_microvolts()
    assert abs(uv[0]) < 1e-9
    # one count is (3.3/4096)/329.7 = 2.443 uV at the skin
    assert abs(uv[1] - 2.443) < 0.01


# ------------------------------------------------------------ stream parser
def test_parser_yields_consecutive_frames():
    payload = b"".join(
        protocol.encode_frame(i, i * 20, np.full(20, 2048, np.uint16))
        for i in range(5))
    p = protocol.FrameStreamParser()
    frames = list(p.feed(payload))
    assert [f.seq for f in frames] == [0, 1, 2, 3, 4]
    assert p.stats["frames_ok"] == 5
    assert p.stats["dropped"] == 0


def test_parser_reassembles_split_frames():
    payload = b"".join(
        protocol.encode_frame(i, 0, np.full(20, 2048, np.uint16))
        for i in range(3))
    p = protocol.FrameStreamParser()
    got = []
    for i in range(0, len(payload), 7):        # deliberately not frame-aligned
        got.extend(p.feed(payload[i : i + 7]))
    assert len(got) == 3


def test_parser_resyncs_after_garbage():
    good = protocol.encode_frame(1, 0, np.full(20, 2048, np.uint16))
    p = protocol.FrameStreamParser()
    frames = list(p.feed(b"\x00\x11\x22rubbish" + good))
    assert len(frames) == 1
    assert p.resyncs >= 1


def test_parser_counts_crc_errors_without_dying():
    good = protocol.encode_frame(1, 0, np.full(20, 2048, np.uint16))
    bad = bytearray(protocol.encode_frame(2, 0, np.full(20, 2048, np.uint16)))
    bad[20] ^= 0xFF
    after = protocol.encode_frame(3, 0, np.full(20, 2048, np.uint16))
    p = protocol.FrameStreamParser()
    frames = list(p.feed(good + bytes(bad) + after))
    assert p.crc_errors >= 1
    assert [f.seq for f in frames] == [1, 3]


def test_parser_counts_dropped_frames():
    p = protocol.FrameStreamParser()
    list(p.feed(protocol.encode_frame(10, 0, np.zeros(20, np.uint16))))
    list(p.feed(protocol.encode_frame(14, 0, np.zeros(20, np.uint16))))
    assert p.dropped == 3


# ------------------------------------------------------------------ metrics
def test_link_gate_rejects_crc_errors():
    s = metrics.LinkStats(frames_ok=3000, crc_errors=2, elapsed_s=60.0)
    ok, why = s.gate_passes()
    assert not ok and any("CRC" in w for w in why)


def test_link_gate_passes_a_clean_minute():
    s = metrics.LinkStats(frames_ok=3000, crc_errors=0, dropped=0, elapsed_s=60.0)
    ok, why = s.gate_passes()
    assert ok, why
    assert abs(s.sample_rate_hz - 1000.0) < 1.0
    assert abs(s.frame_rate_hz - 50.0) < 0.1


def test_analyse_recovers_known_amplitude():
    rng = np.random.default_rng(1)
    x = protocol.ADC_MID + rng.normal(0, 25.0, 20000)
    st = metrics.analyse(x)
    assert abs(st.mean_counts - protocol.ADC_MID) < 1.0
    assert abs(st.rms_counts - 25.0) < 1.5
    assert st.saturated_fraction == 0.0


def test_analyse_flags_saturation():
    x = np.full(2000, 4095.0)
    assert metrics.analyse(x).saturated_fraction == 1.0


def test_analyse_detects_mains_domination():
    t = np.arange(4000) / 1000.0
    mains = protocol.ADC_MID + 100 * np.sin(2 * np.pi * 50 * t)
    rng = np.random.default_rng(2)
    clean = protocol.ADC_MID + rng.normal(0, 30.0, 4000)
    assert metrics.analyse(mains).mains_ratio > 0.5
    assert metrics.analyse(clean).mains_ratio < 0.2


def test_calibration_gate_and_suggestion():
    c = metrics.Calibration(rest_rms=3.0, contract_rms=60.0)
    ok, why = c.gate_passes()
    assert ok, why
    assert abs(c.ratio - 20.0) < 1e-6
    cfg = c.suggest_grip_config()
    assert cfg["rest_level"] == 3.0
    assert c.rest_rms < cfg["max_level"] < c.contract_rms


def test_calibration_gate_rejects_weak_separation():
    ok, why = metrics.Calibration(rest_rms=10.0, contract_rms=12.0).gate_passes()
    assert not ok and any("ratio" in w for w in why)
    ok, why = metrics.Calibration(rest_rms=10.0, contract_rms=4.0).gate_passes()
    assert not ok and any("not above rest" in w for w in why)


# ---------------------------------------------------------------- transport
def test_loopback_and_simulator_drive_the_whole_pipeline():
    payload = transport.simulate_emg(100, contracted=False)
    p = protocol.FrameStreamParser()
    rec = metrics.Recorder()
    with transport.LoopbackTransport(payload, chunk=13) as t:
        while True:
            chunk = t.read()
            if not chunk:
                break
            for f in p.feed(chunk):
                rec.add(f)
    assert p.frames_ok == 100
    assert p.crc_errors == 0 and p.dropped == 0
    assert rec.array.size == 2000


def test_simulator_separates_rest_from_contraction():
    rest = metrics.analyse(_decode_all(transport.simulate_emg(200, contracted=False)))
    con = metrics.analyse(_decode_all(transport.simulate_emg(200, contracted=True)))
    assert con.rms_counts > 5 * rest.rms_counts


def _decode_all(payload: bytes) -> np.ndarray:
    p = protocol.FrameStreamParser()
    rec = metrics.Recorder()
    for f in p.feed(payload):
        rec.add(f)
    return rec.array


def test_serial_transport_reports_missing_pyserial_clearly():
    pytest.importorskip  # noqa - documented intent
    try:
        import serial  # noqa: F401
        pytest.skip("pyserial is installed here, so the error path cannot run")
    except ImportError:
        pass
    with pytest.raises(ImportError, match="pyserial"):
        transport.SerialTransport(port="/dev/null").open()


def test_context_manager_closes_on_exception():
    t = transport.LoopbackTransport(b"")
    with pytest.raises(RuntimeError):
        with t:
            assert t.is_open
            raise RuntimeError("boom")
    assert not t.is_open


# ------------------------------------------------------- C <-> Python parity
def test_c_and_python_agree_byte_for_byte(tmp_path):
    """The failure this guards against is a link that looks alive and
    carries nothing, because the firmware and the host disagree about the
    wire format. Compile the C encoder, run it, decode with Python."""
    src = tmp_path / "emit.c"
    src.write_text(r"""
#include <stdio.h>
#include "protocol.h"
int main(void) {
    uint16_t s[20];
    for (int i = 0; i < 20; i++) s[i] = (uint16_t)(1900 + i * 11);
    uint8_t buf[64];
    size_t n = p1_encode(buf, 0xC0DE, 0xDEADBEEF, 0x05, s, 20);
    fwrite(buf, 1, n, stdout);
    return 0;
}
""")
    exe = tmp_path / "emit"
    inc = ROOT / "firmware" / "pico_emg" / "include"
    r = subprocess.run(["cc", "-std=c11", "-I", str(inc), "-o", str(exe), str(src)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"no C compiler available: {r.stderr[:200]}")
    raw = subprocess.run([str(exe)], capture_output=True).stdout

    assert len(raw) == 54, "C encoder produced the wrong frame length"
    frame = protocol.decode_frame(raw)          # Python must accept it
    assert frame.seq == 0xC0DE
    assert frame.t_us == 0xDEADBEEF
    assert frame.flags == 0x05
    expected = np.array([1900 + i * 11 for i in range(20)], dtype=np.uint16)
    assert np.array_equal(frame.samples, expected)

    # ...and the Python encoder must reproduce the C bytes exactly
    mine = protocol.encode_frame(0xC0DE, 0xDEADBEEF, expected, flags=0x05)
    assert mine == raw, "Python and C encoders disagree byte for byte"
