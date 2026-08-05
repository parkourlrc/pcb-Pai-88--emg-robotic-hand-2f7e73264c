"""PICO1 wire protocol - host side.

This is the mirror of firmware/pico_emg/include/protocol.h. The two are
written independently and MUST agree byte for byte; tests/test_parity.py
compiles the C encoder and decodes its output with this module, because a
protocol mismatch is the failure that produces a link which looks alive and
carries nothing.

Frame layout, little-endian:

    offset  size   field
     0      2      magic 0xE2 0xA2
     2      1      version = 1
     3      1      flags
     4      2      seq (uint16, wraps)
     6      4      t_us of the first sample
    10      1      n
    11      1      reserved
    12      n*2    samples, raw 12-bit ADC
    12+n*2 2       CRC-16/CCITT-FALSE over bytes [0, 12+n*2)

With n = 20 a frame is 54 bytes and the link carries 50 frames/s.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntFlag

import numpy as np

MAGIC = b"\xe2\xa2"
VERSION = 1
HEADER_SIZE = 12
CRC_SIZE = 2
DEFAULT_N = 20
SAMPLE_RATE_HZ = 1000
FRAME_RATE_HZ = SAMPLE_RATE_HZ / DEFAULT_N

# Board constants. Total analog gain is 20.61 (AD623, R9 = 5.1k) x 16
# (U2A, R4 = 1k / R5 = 15k) = 329.7. The schematic's old "21 x 48 = 1000"
# note was stale; do not reintroduce it.
VREF = 3.3
ADC_BITS = 12
ADC_MAX = 4095
ADC_MID = 2048
TOTAL_GAIN = 329.7
UV_PER_COUNT = (VREF / 4096.0) / TOTAL_GAIN * 1e6  # 2.443 uV at the skin


class Flags(IntFlag):
    SATURATED_HIGH = 0x01
    SATURATED_LOW = 0x02
    CONTACT_SUSPECT = 0x04
    OVERRUN = 0x08


class FrameError(ValueError):
    """Base class so a caller can tell corrupt from merely truncated."""


class BadMagic(FrameError):
    pass


class BadVersion(FrameError):
    pass


class BadLength(FrameError):
    pass


class BadCRC(FrameError):
    pass


def frame_size(n: int = DEFAULT_N) -> int:
    return HEADER_SIZE + n * 2 + CRC_SIZE


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no
    final xor. crc16(b"123456789") == 0x29B1."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    seq: int
    t_us: int
    flags: int
    samples: np.ndarray  # uint16, raw ADC counts

    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def saturated(self) -> bool:
        return bool(self.flags & (Flags.SATURATED_HIGH | Flags.SATURATED_LOW))

    @property
    def contact_suspect(self) -> bool:
        return bool(self.flags & Flags.CONTACT_SUSPECT)

    @property
    def overrun(self) -> bool:
        return bool(self.flags & Flags.OVERRUN)

    def to_volts(self) -> np.ndarray:
        """Counts referred to the ADC pin."""
        return self.samples.astype(np.float64) * (VREF / 4096.0)

    def to_microvolts(self) -> np.ndarray:
        """Counts referred to the SKIN: mid-scale is 0 uV."""
        centred = self.samples.astype(np.float64) - ADC_MID
        return centred * UV_PER_COUNT


def encode_frame(seq: int, t_us: int, samples, flags: int = 0) -> bytes:
    """Present so the tests (and the simulator) can generate real traffic
    without a Pico attached."""
    arr = np.asarray(samples, dtype=np.uint16)
    if len(arr) > 255:
        raise ValueError("a frame carries at most 255 samples")
    body = struct.pack(
        "<2sBBHIBB", MAGIC, VERSION, flags & 0xFF, seq & 0xFFFF,
        t_us & 0xFFFFFFFF, len(arr), 0
    ) + arr.astype("<u2").tobytes()
    return body + struct.pack("<H", crc16(body))


def decode_frame(buf: bytes) -> Frame:
    """Strict decode. Raises a specific FrameError subclass."""
    if len(buf) < HEADER_SIZE + CRC_SIZE:
        raise BadLength(f"{len(buf)} bytes is shorter than an empty frame")
    if buf[0:2] != MAGIC:
        raise BadMagic(f"magic {buf[0:2]!r}")
    if buf[2] != VERSION:
        raise BadVersion(f"version {buf[2]}")
    n = buf[10]
    need = frame_size(n)
    if len(buf) < need:
        raise BadLength(f"need {need} bytes for n={n}, have {len(buf)}")
    want = struct.unpack_from("<H", buf, need - CRC_SIZE)[0]
    if crc16(buf[: need - CRC_SIZE]) != want:
        raise BadCRC("checksum mismatch")
    flags = buf[3]
    seq = struct.unpack_from("<H", buf, 4)[0]
    t_us = struct.unpack_from("<I", buf, 6)[0]
    samples = np.frombuffer(buf, dtype="<u2", count=n, offset=HEADER_SIZE).copy()
    return Frame(seq=seq, t_us=t_us, flags=flags, samples=samples)


def try_decode(buf: bytes):
    try:
        return decode_frame(buf)
    except FrameError:
        return None


def seq_gap(prev: int, now: int) -> int:
    """Frames missed between prev and now, surviving the uint16 wrap.
    65535 -> 0 is a gap of zero, not 65535."""
    return ((now - prev) & 0xFFFF) - 1


class FrameStreamParser:
    """Feed it arbitrary byte chunks; get whole Frames out.

    A serial link hands you partial frames, and noise on connect will put
    garbage in front of the first good one, so resynchronising by scanning
    for the magic is the normal case rather than an error path.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames_ok = 0
        self.crc_errors = 0
        self.resyncs = 0
        self.dropped = 0
        self._last_seq: int | None = None

    def feed(self, chunk: bytes):
        """Yields every complete Frame available after adding chunk."""
        self._buf.extend(chunk)
        while True:
            start = self._buf.find(MAGIC)
            if start < 0:
                # keep a trailing byte: the magic may straddle the boundary
                if len(self._buf) > 1:
                    del self._buf[: len(self._buf) - 1]
                return
            if start > 0:
                del self._buf[:start]
                self.resyncs += 1
            if len(self._buf) < HEADER_SIZE + CRC_SIZE:
                return
            n = self._buf[10]
            need = frame_size(n)
            if len(self._buf) < need:
                return
            candidate = bytes(self._buf[:need])
            try:
                frame = decode_frame(candidate)
            except BadCRC:
                self.crc_errors += 1
                # Drop only the magic, not the whole frame: the real frame
                # may start inside what we just looked at.
                del self._buf[:2]
                continue
            except FrameError:
                del self._buf[:2]
                self.resyncs += 1
                continue
            del self._buf[:need]
            self.frames_ok += 1
            if self._last_seq is not None:
                gap = seq_gap(self._last_seq, frame.seq)
                if gap > 0:
                    self.dropped += gap
            self._last_seq = frame.seq
            yield frame

    @property
    def stats(self) -> dict:
        return {
            "frames_ok": self.frames_ok,
            "crc_errors": self.crc_errors,
            "resyncs": self.resyncs,
            "dropped": self.dropped,
        }
