"""Small synthesized sound set (numpy, no third-party audio) -> assets/sfx/{whoosh,pop,tick}.wav,
plus the optional music bed from channel.json `music` (path, or {"src", "volume"}) copied to assets/sfx/music.*."""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

import numpy as np

from common import ROOT, Job, PipelineError, file_sig, load_json, log, save_json, sha1

SR = 44100
VERSION = 1


def _t(sec):
    return np.arange(int(SR * sec)) / SR


def _lowpass(x, fc):
    """One-pole low-pass; fc may vary per sample."""
    a = np.exp(-2 * np.pi * np.broadcast_to(np.asarray(fc, float), x.shape) / SR)
    try:
        from scipy.signal import lfilter
        if np.ndim(fc) == 0:
            return lfilter([1 - a[0]], [1, -a[0]], x)
    except ImportError:
        pass
    y, s = np.empty_like(x), 0.0
    for i in range(len(x)):
        s = (1 - a[i]) * x[i] + a[i] * s
        y[i] = s
    return y


def whoosh(dur=0.45):
    n = int(SR * dur)
    x = np.random.default_rng(1).standard_normal(n)
    sweep = np.geomspace(300, 7000, n)
    return (_lowpass(x, sweep) - _lowpass(x, sweep * 0.25)) * np.sin(np.linspace(0, np.pi, n)) ** 2


def pop():
    t = _t(0.07)
    return np.sin(2 * np.pi * np.cumsum(np.linspace(900, 300, len(t))) / SR) * np.exp(-t * 70)


def tick():
    t = _t(0.05)
    return np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 120)


def save(path, x, peak=0.9):
    x = x / (np.max(np.abs(x)) + 1e-9) * peak
    st = np.stack([x, x], 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(st, -1, 1) * 32767).astype(np.int16).tobytes())


SOUNDS = {"whoosh": whoosh, "pop": pop, "tick": tick}


def music_source(channel: dict) -> tuple[Path | None, float]:
    m = channel.get("music")
    if not m:
        return None, 0.0
    src, vol = (m.get("src"), float(m.get("volume", 0.16))) if isinstance(m, dict) else (m, 0.16)
    p = Path(src)
    return (p if p.is_absolute() else ROOT / p), vol


def run_sfx(job: Job, force: bool = False) -> dict:
    out, info_p = job.a("sfx"), job.b("sfx_info.json")
    music, vol = music_source(job.channel)
    if music is not None and not music.exists():
        raise PipelineError(f"channel.json music file not found: {music}")
    sig = sha1(VERSION, file_sig(music) if music else None)
    if not force and info_p.exists() and load_json(info_p).get("sig") == sig and \
            all((out / f"{n}.wav").exists() for n in SOUNDS):
        log.info("sfx: up to date")
        return load_json(info_p)
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in SOUNDS.items():
        save(out / f"{name}.wav", fn())
    info = {"sig": sig, "files": [f"sfx/{n}.wav" for n in SOUNDS], "music": None}
    if music:
        for old in out.glob("music.*"):
            old.unlink()
        shutil.copy2(music, out / f"music{music.suffix.lower()}")
        info["music"] = {"src": f"sfx/music{music.suffix.lower()}", "volume": vol}
    save_json(info_p, info)
    log.info(f"sfx: {len(SOUNDS)} sounds" + (f" + music ({music.name})" if music else "") + " -> assets/sfx/")
    return info
