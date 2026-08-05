"""Byte sources feeding the PICO1 parser.

pyserial is imported lazily inside SerialTransport, so everything here -
including the whole test suite and `cli selftest` - works on a machine that
does not have it installed, which is the state of this Mac.
"""
from __future__ import annotations

import abc

import numpy as np

from .protocol import ADC_MID, DEFAULT_N, SAMPLE_RATE_HZ, encode_frame

RP2040_VID = 0x2E8A  # Raspberry Pi


class Transport(abc.ABC):
    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def read(self, max_bytes: int = 4096) -> bytes: ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class LoopbackTransport(Transport):
    """Replays a fixed byte string. The test workhorse."""

    def __init__(self, payload: bytes, chunk: int = 64):
        self._payload = payload
        self._chunk = chunk
        self._pos = 0
        self.is_open = False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def read(self, max_bytes: int = 4096) -> bytes:
        n = min(self._chunk, max_bytes, len(self._payload) - self._pos)
        if n <= 0:
            return b""
        out = self._payload[self._pos : self._pos + n]
        self._pos += n
        return out


class SerialTransport(Transport):
    """Pico USB CDC."""

    def __init__(self, port: str | None = None, baudrate: int = 921600,
                 timeout: float = 0.05):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser = None

    @staticmethod
    def autodetect_port() -> str | None:
        """Find an RP2040 by USB VID. Returns None if pyserial is missing
        or nothing matches - callers should say so rather than crash."""
        try:
            from serial.tools import list_ports
        except ImportError:
            return None
        for p in list_ports.comports():
            if getattr(p, "vid", None) == RP2040_VID:
                return p.device
        return None

    def open(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise ImportError(
                "pyserial is required to talk to the Pico. Install it with:\n"
                "    python3 -m pip install pyserial\n"
                "Everything else in this package (selftest, analysis, the "
                "test suite) works without it."
            ) from exc
        port = self.port or self.autodetect_port()
        if port is None:
            raise RuntimeError(
                "No RP2040 found. Check the Pico is plugged in and running "
                "the firmware, or pass --port explicitly."
            )
        self._ser = serial.Serial(port, self.baudrate, timeout=self.timeout)
        self.port = port

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def read(self, max_bytes: int = 4096) -> bytes:
        if self._ser is None:
            return b""
        waiting = getattr(self._ser, "in_waiting", 0)
        return self._ser.read(max(1, min(max_bytes, waiting or 1)))


def simulate_emg(n_frames: int, *, contracted: bool = False,
                 rest_counts: float = 3.0, contract_counts: float = 60.0,
                 mains_counts: float = 0.0, seed: int = 0,
                 start_seq: int = 0) -> bytes:
    """Generate a realistic PICO1 byte stream with no hardware attached.

    Not a substitute for a bench test - it is a way to exercise the whole
    host pipeline end to end and to give `cli selftest` something to prove
    itself against. Real sEMG is modelled as band-limited noise, which is a
    fair approximation of its amplitude statistics even though it says
    nothing about motor-unit structure.
    """
    rng = np.random.default_rng(seed)
    amp = contract_counts if contracted else rest_counts
    out = bytearray()
    t_us = 0
    for i in range(n_frames):
        noise = rng.normal(0.0, amp, DEFAULT_N)
        if mains_counts:
            t = (np.arange(DEFAULT_N) + i * DEFAULT_N) / SAMPLE_RATE_HZ
            noise = noise + mains_counts * np.sin(2 * np.pi * 50.0 * t)
        counts = np.clip(np.rint(ADC_MID + noise), 0, 4095).astype(np.uint16)
        out += encode_frame((start_seq + i) & 0xFFFF, t_us, counts)
        t_us += int(DEFAULT_N * 1_000_000 / SAMPLE_RATE_HZ)
    return bytes(out)
