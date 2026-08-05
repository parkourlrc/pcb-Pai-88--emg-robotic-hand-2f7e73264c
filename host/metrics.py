"""Measurements for bring-up stages 4 and 5.

Every number this module produces corresponds to a blank in
board/revd/bringup.md. That is the design brief: the operator should be
able to run the tool, read the output, and fill the document in, rather
than having to work out what to measure.

Pure numpy/scipy - no hardware, no serial - so it is fully testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from .protocol import ADC_MID, SAMPLE_RATE_HZ, UV_PER_COUNT


@dataclass
class LinkStats:
    """Bring-up stage 4: is the link trustworthy?"""
    frames_ok: int = 0
    crc_errors: int = 0
    dropped: int = 0
    resyncs: int = 0
    elapsed_s: float = 0.0

    @property
    def frame_rate_hz(self) -> float:
        return self.frames_ok / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def sample_rate_hz(self) -> float:
        from .protocol import DEFAULT_N
        return self.frame_rate_hz * DEFAULT_N

    @property
    def error_rate(self) -> float:
        total = self.frames_ok + self.crc_errors
        return self.crc_errors / total if total else 0.0

    def gate_passes(self) -> tuple[bool, list[str]]:
        """Stage 4's gate: a clean minute, baseline near mid-scale."""
        why = []
        if self.crc_errors:
            why.append(f"{self.crc_errors} CRC errors (expect 0)")
        if self.dropped:
            why.append(f"{self.dropped} dropped frames (expect 0)")
        if self.elapsed_s >= 5.0 and not (995 <= self.sample_rate_hz <= 1005):
            why.append(f"sample rate {self.sample_rate_hz:.1f} Hz (expect 1000 +/- 5)")
        return (not why), why


@dataclass
class SignalStats:
    """Bring-up stages 4 and 5: is the signal itself sane?"""
    mean_counts: float = 0.0
    pk_pk_counts: float = 0.0
    rms_counts: float = 0.0
    mains_ratio: float = 0.0
    saturated_fraction: float = 0.0

    @property
    def mean_offset_counts(self) -> float:
        return self.mean_counts - ADC_MID

    @property
    def pk_pk_uv(self) -> float:
        return self.pk_pk_counts * UV_PER_COUNT

    @property
    def rms_uv(self) -> float:
        return self.rms_counts * UV_PER_COUNT


def analyse(counts: np.ndarray, fs: float = SAMPLE_RATE_HZ) -> SignalStats:
    """Summarise a block of raw ADC counts."""
    x = np.asarray(counts, dtype=np.float64)
    if x.size == 0:
        return SignalStats()
    centred = x - ADC_MID
    sat = np.count_nonzero((x >= 4090) | (x <= 5)) / x.size

    mains = 0.0
    # Welch needs a few cycles of 50 Hz to resolve it at all; below that the
    # ratio is meaningless and reporting 0 is honest.
    if x.size >= int(fs):
        nper = min(1024, x.size)
        f, p = signal.welch(centred, fs=fs, nperseg=nper)
        band = (f >= 48) & (f <= 52)
        total = np.trapezoid(p, f) if hasattr(np, "trapezoid") else np.trapz(p, f)
        if total > 0:
            mains = float(np.trapezoid(p[band], f[band]) if hasattr(np, "trapezoid")
                          else np.trapz(p[band], f[band])) / float(total)

    return SignalStats(
        mean_counts=float(x.mean()),
        pk_pk_counts=float(x.max() - x.min()),
        rms_counts=float(np.sqrt(np.mean(centred ** 2))),
        mains_ratio=mains,
        saturated_fraction=float(sat),
    )


@dataclass
class Calibration:
    """Stage 5: the two levels grip_calibrate() needs."""
    rest_rms: float = 0.0
    contract_rms: float = 0.0

    @property
    def ratio(self) -> float:
        return self.contract_rms / self.rest_rms if self.rest_rms > 0 else 0.0

    def gate_passes(self, min_ratio: float = 3.0) -> tuple[bool, list[str]]:
        """Stage 5 wants contraction unambiguously distinct from rest.

        Below about 3:1 no downstream controller can separate them
        reliably, and the honest response is to fix the electrodes rather
        than to tune thresholds until it looks like it works.
        """
        why = []
        if self.contract_rms <= self.rest_rms:
            why.append("contraction is not above rest - check electrode placement")
        elif self.ratio < min_ratio:
            why.append(f"contraction/rest ratio {self.ratio:.1f} is below {min_ratio}")
        if self.rest_rms <= 0:
            why.append("resting level is zero - the link or the front end is dead")
        return (not why), why

    def suggest_grip_config(self) -> dict:
        """Values to paste into grip_calibrate() on the Pico.

        max_level sits slightly below the measured hard squeeze so the
        gripper reaches fully closed without the operator having to
        maintain a maximal contraction, which is tiring and drifts.
        """
        return {
            "rest_level": round(self.rest_rms, 2),
            "max_level": round(self.rest_rms + 0.85 * (self.contract_rms - self.rest_rms), 2),
        }


@dataclass
class Recorder:
    """Accumulates frames so a whole capture can be analysed at the end."""
    counts: list = field(default_factory=list)

    def add(self, frame) -> None:
        self.counts.append(np.asarray(frame.samples, dtype=np.float64))

    @property
    def array(self) -> np.ndarray:
        if not self.counts:
            return np.empty(0, dtype=np.float64)
        return np.concatenate(self.counts)

    def analyse(self) -> SignalStats:
        return analyse(self.array)
