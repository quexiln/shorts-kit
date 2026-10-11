"""Generate synthetic placeholder media for the demo composition.

Writes into remotion/public/demo/: two video clips (ffmpeg test sources), two images (Pillow),
a tone "voice" that follows the word timings in src/demo.ts, a soft music bed and a pop effect.
Nothing here depicts real people, places or brands.

Usage (from remotion/):  python scripts/make_demo_media.py
Requires: ffmpeg on PATH, numpy, Pillow.
"""

from __future__ import annotations

import re
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "demo"
SR = 48000


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def clip(path: Path, w: int, h: int, sec: float, c0: str, c1: str) -> None:
    """Slowly moving two-colour gradient with a drifting rectangle and a static band."""
    box_w, box_h = w // 4, h // 5
    vf = (
        f"drawbox=x='(w-{box_w})*(0.5+0.4*sin(t*0.9))':y='h*0.45':w={box_w}:h={box_h}:color=white@0.35:t=fill,"
        f"drawbox=x=0:y=ih*0.78:w=iw:h=ih*0.06:color=black@0.25:t=fill,format=yuv420p"
    )
    ffmpeg(
        "-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:speed=0.02:duration={sec}:rate=30",
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(path),
    )


def gradient(w: int, h: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    t = np.linspace(0, 1, h)[:, None, None]
    rows = (np.array(top) * (1 - t) + np.array(bottom) * t).astype(np.uint8)
    return Image.fromarray(np.repeat(rows, w, axis=1))


def image_blocks(path: Path, w: int, h: int, top, bottom) -> None:
    """Abstract skyline of plain blocks with lit windows."""
    img = gradient(w, h, top, bottom)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(7)
    x = 0
    while x < w:
        bw = int(rng.integers(w // 10, w // 5))
        bh = int(rng.integers(h // 4, h // 2))
        d.rectangle([x, h - bh, x + bw - 8, h], fill=(30, 34, 44))
        for wy in range(h - bh + 30, h - 30, 60):
            for wx in range(x + 20, x + bw - 40, 50):
                if rng.random() < 0.5:
                    d.rectangle([wx, wy, wx + 22, wy + 30], fill=(255, 214, 120))
        x += bw
    d.ellipse([int(w * 0.7), int(h * 0.1), int(w * 0.7) + w // 8, int(h * 0.1) + w // 8], fill=(240, 236, 220))
    img.save(path, quality=90)


def image_circles(path: Path, w: int, h: int) -> None:
    img = gradient(w, h, (44, 62, 80), (22, 28, 36))
    d = ImageDraw.Draw(img)
    for i, (cx, cy, r) in enumerate([(0.35, 0.4, 0.22), (0.62, 0.55, 0.16), (0.45, 0.72, 0.1)]):
        shade = 120 + i * 40
        d.ellipse([int((cx - r) * w), int(cy * h - r * w), int((cx + r) * w), int(cy * h + r * w)], fill=(shade, shade, 170))
    img.save(path, quality=90)


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())


def demo_words() -> list[tuple[str, float, float]]:
    src = (ROOT / "src" / "demo.ts").read_text(encoding="utf-8")
    return [(t, float(a), float(b)) for t, a, b in re.findall(r"w\('([^']*)',\s*([\d.]+),\s*([\d.]+)", src)]


def duration() -> float:
    m = re.search(r"durationSec:\s*([\d.]+)", (ROOT / "src" / "demo.ts").read_text(encoding="utf-8"))
    return float(m.group(1)) if m else 13.0


def voice(words) -> np.ndarray:
    """One soft harmonic tone per word, pitch drifting a little, so timings are audible."""
    out = np.zeros(int(SR * duration()))
    for i, (_, a, b) in enumerate(words):
        n = int((b - a) * SR)
        t = np.arange(n) / SR
        f0 = 150 + 25 * np.sin(i * 1.7)
        tone = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3))
        env = np.minimum(1, np.minimum(t / 0.02, (t[-1] - t) / 0.04 + 1e-9))
        s = int(a * SR)
        out[s:s + n] += 0.25 * tone * env
    return out


def music(sec: float = 8.0) -> np.ndarray:
    t = np.arange(int(SR * sec)) / SR
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63))
    return 0.15 * chord * (0.8 + 0.2 * np.sin(2 * np.pi * 0.25 * t))


def pop() -> np.ndarray:
    t = np.arange(int(SR * 0.12)) / SR
    return 0.6 * np.sin(2 * np.pi * (900 - 3000 * t) * t) * np.exp(-t * 40)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    clip(OUT / "v01.mp4", 1920, 1080, 6, "0x2b4a6f", "0x6f8fb0")
    clip(OUT / "v03.mp4", 1080, 1920, 3, "0x3d5a40", "0x8fae7a")
    image_blocks(OUT / "v02.jpg", 1600, 1200, (70, 90, 140), (230, 160, 120))
    image_circles(OUT / "v04.jpg", 1440, 1920)
    write_wav(OUT / "voice.wav", voice(demo_words()))
    write_wav(OUT / "music.wav", music())
    write_wav(OUT / "pop.wav", pop())
    print(f"demo media written to {OUT}")


if __name__ == "__main__":
    main()
