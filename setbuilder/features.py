"""Audio feature extraction.

Everything energy-related is computed on the **middle 60%** of the track, so a
long ambient intro or a filtered outro does not drag the numbers down.  Tempo
uses the whole signal, where the beat tracker has more to work with.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

SAMPLE_RATE = 22050
N_FFT = 4096
HOP_LENGTH = 1024

# Frequency bands, in Hz.
BANDS: List[Tuple[str, float, float]] = [
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 250.0),
    ("mid", 250.0, 4000.0),
    ("high", 4000.0, SAMPLE_RATE / 2),
]

# The ordered feature vector handed to the ridge model.  Order is part of the
# stored model payload, so append to the end rather than reordering.
FEATURE_NAMES: List[str] = [
    "rms_sub", "rms_bass", "rms_mid", "rms_high",
    "onset_rate", "flux_mean", "flux_var", "percussive_ratio",
    "loudness_range", "fullness", "rms_overall", "bpm",
]

MIDDLE_FRACTION = 0.60


def middle_slice(y: np.ndarray, sr: int,
                 fraction: float = MIDDLE_FRACTION) -> np.ndarray:
    """The centre ``fraction`` of a signal, with a floor so short clips survive."""
    total = len(y)
    if total == 0:
        return y
    want = int(total * fraction)
    # A 30s preview is already all "middle"; don't shave it down to nothing.
    minimum = min(total, int(20 * sr))
    want = max(want, minimum)
    start = max(0, (total - want) // 2)
    return y[start:start + want]


def load_audio(path: str, sr: int = SAMPLE_RATE,
               max_seconds: Optional[float] = None) -> Tuple[np.ndarray, int]:
    import librosa
    y, actual_sr = librosa.load(path, sr=sr, mono=True, duration=max_seconds)
    return y, actual_sr


def detect_bpm(y: np.ndarray, sr: int, start_bpm: float = 128.0) -> Tuple[float, float]:
    """Return (corrected_bpm, raw_bpm) using librosa's beat tracker."""
    import librosa

    from .bpm import correct_bpm

    if len(y) < sr:
        return 0.0, 0.0
    tempo, _beats = librosa.beat.beat_track(
        y=y, sr=sr, start_bpm=start_bpm, hop_length=512, units="frames"
    )
    raw = float(np.atleast_1d(tempo)[0])
    return correct_bpm(raw), raw


# --- key detection -------------------------------------------------------

# Krumhansl-Schmuckler key profiles, used when Essentia is unavailable.
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                      2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                      2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def detect_key_essentia(y: np.ndarray, sr: int) -> Optional[Tuple[str, str, float]]:
    """Key via Essentia's KeyExtractor with the ``edma`` profile.

    ``edma`` is trained on electronic dance music, which is the whole library
    here.  Returns (tonic, scale, strength) or None if Essentia is missing.
    """
    try:
        import essentia
        import essentia.standard as es
    except Exception:
        return None
    try:
        essentia.log.infoActive = False
        essentia.log.warningActive = False
    except Exception:
        pass
    try:
        signal = np.ascontiguousarray(y, dtype=np.float32)
        extractor = es.KeyExtractor(profileType="edma", sampleRate=float(sr))
        tonic, scale, strength = extractor(signal)
        if not tonic:
            return None
        return str(tonic), str(scale), float(strength)
    except Exception:
        return None


def detect_key_chroma(y: np.ndarray, sr: int) -> Tuple[str, str, float]:
    """Krumhansl-Schmuckler correlation over a CQT chromagram."""
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP_LENGTH)
    profile = chroma.mean(axis=1)
    if profile.sum() <= 0:
        return "C", "major", 0.0
    profile = profile / profile.sum()

    best = ("C", "major", -2.0)
    for tonic_index in range(12):
        rotated = np.roll(profile, -tonic_index)
        for scale, reference in (("major", _KS_MAJOR), ("minor", _KS_MINOR)):
            correlation = float(np.corrcoef(rotated, reference)[0, 1])
            if math.isnan(correlation):
                continue
            if correlation > best[2]:
                best = (_PITCH_NAMES[tonic_index], scale, correlation)
    return best


def detect_key(y: np.ndarray, sr: int) -> Tuple[Optional[str], str, float]:
    """Return (camelot, source, strength).  Prefers Essentia, falls back to chroma."""
    from .camelot import pitch_class_to_camelot

    result = detect_key_essentia(y, sr)
    source = "essentia"
    if result is None:
        result = detect_key_chroma(y, sr)
        source = "chroma"

    tonic, scale, strength = result
    pitch_class = _PITCH_NAMES.index(tonic) if tonic in _PITCH_NAMES else None
    if pitch_class is None:
        # Essentia may spell a flat, e.g. "Eb".
        from .camelot import _PITCH_CLASSES
        pitch_class = _PITCH_CLASSES.get(tonic.lower())
    if pitch_class is None:
        return None, source, 0.0
    return pitch_class_to_camelot(pitch_class, scale), source, strength


# --- energy features -----------------------------------------------------

def _db(value: float, floor: float = 1e-10) -> float:
    return 20.0 * math.log10(max(float(value), floor))


def extract_features(y: np.ndarray, sr: int, bpm: float = 0.0) -> Dict[str, float]:
    """Compute the energy feature set on the middle of the track."""
    import librosa

    middle = middle_slice(y, sr)
    if len(middle) < sr // 2:
        return {name: 0.0 for name in FEATURE_NAMES}

    stft = np.abs(librosa.stft(middle, n_fft=N_FFT, hop_length=HOP_LENGTH))
    frequencies = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    power = stft ** 2

    features: Dict[str, float] = {}

    # Per-band RMS, in dBFS.  The 2/n_fft**2 factor is librosa's own rms
    # normalisation, so band levels sit on the same scale as rms_overall
    # (and roughly sum to it) rather than on the raw, window-inflated one.
    scale = 2.0 / (N_FFT ** 2)
    for name, low, high in BANDS:
        mask = (frequencies >= low) & (frequencies < high)
        if not mask.any():
            features[f"rms_{name}"] = -100.0
            continue
        band_rms = np.sqrt(power[mask].sum(axis=0) * scale)
        features[f"rms_{name}"] = _db(float(band_rms.mean()))

    # Overall RMS and its distribution.
    rms = librosa.feature.rms(S=stft, frame_length=N_FFT, hop_length=HOP_LENGTH)[0]
    features["rms_overall"] = _db(float(rms.mean()))
    if rms.size:
        p95 = float(np.percentile(rms, 95))
        p10 = float(np.percentile(rms, 10))
        features["loudness_range"] = _db(p95) - _db(p10)
        peak = float(rms.max())
        features["fullness"] = (float((rms > 0.8 * peak).mean())
                                if peak > 0 else 0.0)
    else:
        features["loudness_range"] = 0.0
        features["fullness"] = 0.0

    # Onset rate, in onsets per second.
    onset_envelope = librosa.onset.onset_strength(S=librosa.amplitude_to_db(stft),
                                                  sr=sr, hop_length=HOP_LENGTH)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_envelope, sr=sr,
                                        hop_length=HOP_LENGTH, units="time")
    duration = len(middle) / sr
    features["onset_rate"] = float(len(onsets)) / duration if duration > 0 else 0.0

    # Spectral flux: mean and variance of the positive spectral difference.
    flux = np.diff(stft, axis=1)
    flux = np.maximum(flux, 0.0).sum(axis=0)
    if flux.size:
        scale = float(stft.sum(axis=0).mean()) or 1.0
        flux = flux / scale
        features["flux_mean"] = float(flux.mean())
        features["flux_var"] = float(flux.var())
    else:
        features["flux_mean"] = 0.0
        features["flux_var"] = 0.0

    # Percussive-to-harmonic ratio via HPSS on the magnitude spectrogram.
    try:
        harmonic, percussive = librosa.decompose.hpss(stft)
        harmonic_energy = float(np.sum(harmonic ** 2))
        percussive_energy = float(np.sum(percussive ** 2))
        total = harmonic_energy + percussive_energy
        features["percussive_ratio"] = (percussive_energy / total) if total > 0 else 0.5
    except Exception:
        features["percussive_ratio"] = 0.5

    features["bpm"] = float(bpm or 0.0)
    return {name: float(features.get(name, 0.0)) for name in FEATURE_NAMES}


def feature_vector(features: Dict[str, Any]) -> np.ndarray:
    """Ordered numeric vector for the model, in :data:`FEATURE_NAMES` order."""
    return np.array([float(features.get(name, 0.0) or 0.0)
                     for name in FEATURE_NAMES], dtype=float)
