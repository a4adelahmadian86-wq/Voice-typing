"""Small, dependency-free PCM helpers for the live speech pipeline."""

from __future__ import annotations

import math
import struct

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # 20 ms at 16 kHz.
FRAME_BYTES = FRAME_SAMPLES * 2


def pcm_frame_rms(frame: bytes) -> float:
    """Return signed-PCM16 RMS for one 20 ms frame."""
    if len(frame) != FRAME_BYTES:
        return 0.0
    samples = struct.unpack("<320h", frame)
    return math.sqrt(sum(sample * sample for sample in samples) / FRAME_SAMPLES)


def estimate_noise_floor(levels: list[float]) -> float:
    """Estimate ambient RMS from the quieter half of a short calibration window."""
    if not levels:
        return 35.0
    ordered = sorted(max(0.0, float(level)) for level in levels)
    quieter = ordered[: max(1, (len(ordered) + 1) // 2)]
    estimate = sum(quieter) / len(quieter)
    return max(25.0, min(250.0, estimate))


def speech_threshold(noise_floor: float) -> float:
    """Conservative adaptive threshold in PCM16 units."""
    return max(140.0, min(1100.0, float(noise_floor) * 2.2))


def normalize_speech_pcm(
    pcm: bytes,
    *,
    min_speech_rms: float = 95.0,
    target_rms: float = 0.07,
    max_gain: float = 4.0,
) -> tuple[bytes, float, float]:
    """Lift quiet speech without clipping; return normalized PCM, gain, speech RMS.

    The level estimate excludes low-energy frames so leading/trailing silence does
    not dominate it. No audio is altered when the signal is already sufficiently
    strong or contains no detectable speech.
    """
    if not pcm or len(pcm) % 2:
        return pcm, 1.0, 0.0

    sample_count = len(pcm) // 2
    samples = struct.unpack(f"<{sample_count}h", pcm)
    active_square_sum = 0
    active_sample_count = 0

    for start in range(0, sample_count, FRAME_SAMPLES):
        frame = samples[start : start + FRAME_SAMPLES]
        if len(frame) != FRAME_SAMPLES:
            break
        square_sum = sum(value * value for value in frame)
        frame_rms = math.sqrt(square_sum / FRAME_SAMPLES)
        if frame_rms >= min_speech_rms:
            active_square_sum += square_sum
            active_sample_count += FRAME_SAMPLES

    if not active_sample_count:
        return pcm, 1.0, 0.0

    speech_rms = math.sqrt(active_square_sum / active_sample_count) / 32768.0
    peak = max(abs(value) for value in samples)
    if speech_rms <= 0.0 or peak <= 0:
        return pcm, 1.0, speech_rms

    requested_gain = max(1.0, min(float(max_gain), target_rms / speech_rms))
    peak_safe_gain = 30000.0 / peak
    gain = min(requested_gain, peak_safe_gain)
    if gain <= 1.01:
        return pcm, 1.0, speech_rms

    normalized = (
        max(-32768, min(32767, int(round(value * gain))))
        for value in samples
    )
    return struct.pack(f"<{sample_count}h", *normalized), gain, speech_rms
