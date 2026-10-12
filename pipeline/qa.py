"""Quality check of final.mp4: 1 fps contact sheet with safe-area overlay (build/qa_sheet.png), black frames,
letterbox bars and thin dark edge lines -> build/qa_report.json. Look at the sheet yourself.
  python pipeline/qa.py <job_dir>                    show the report (exit 3 if findings are unreviewed)
  python pipeline/qa.py <job_dir> --review "reason"  record that the findings were reviewed (false alarm / accepted)"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime

import numpy as np

from common import FPS, SAFE, Job, PipelineError, ffprobe_duration, file_sig, load_json, log, media_size, qa_ok, save_json

TW, TH, COLS = 270, 480, 8


def grab_frames(video, fps: float = 1.0, w: int = TW, h: int = TH) -> list[np.ndarray]:
    """Frames at exactly t = 0, 1/fps, 2/fps ... (select, not the fps filter, which shifts by half a step)."""
    vf = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{1 / fps - 0.001:.3f})',scale={w}:{h}"
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", vf, "-fps_mode", "vfr", "-f",
                          "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (w * h * 3)
    return [np.frombuffer(raw, np.uint8, w * h * 3, i * w * h * 3).reshape(h, w, 3) for i in range(n)]


def _bar_len(lines) -> int:
    """Rows of flat black from the edge (p99 <= 4, std <= 1.2); dark scenes with highlights do not count."""
    n = 0
    for ln in lines:
        if np.percentile(ln, 99) > 4 or ln.std() > 1.2:
            break
        n += 1
    return n


def _straight(lines: np.ndarray, n: int) -> bool:
    """True if the dark band ends at the same depth (+-1 row) in >= 90% of the columns: a real letterbox edge."""
    lit = lines[:n + 3] > 4
    first = np.where(lit.any(0), lit.argmax(0), n + 3)
    return bool((np.abs(first - n) <= 1).mean() >= 0.9)


def frame_checks(fr: np.ndarray) -> list[str]:
    """Black frame, letterbox bars (flat black band with a straight edge) and ambiguous dark bands (flat black but the
    edge is not straight: probably dark footage such as an animation on black space)."""
    lum = fr.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    h, w = lum.shape
    if lum.mean() < 6 and np.percentile(lum, 99) < 20:
        return ["black frame"]
    rows, cols = max(3, int(32 * h / 1920)), max(3, int(20 * w / 1080))
    out = []
    for name, lines, lim in (("top", lum, rows), ("bottom", lum[::-1], rows), ("left", lum.T, cols),
                             ("right", lum.T[::-1], cols)):
        n = _bar_len(lines)
        if n >= lim:
            out.append(f"black bar {name}" if _straight(lines, n)
                       else f"dark band at {name} — letterbox or dark footage? check the frame")
    return out


def edge_lines(gray: np.ndarray, max_rows: int = 24) -> list[str]:
    """Thin dark line at a frame edge (e.g. a broadcaster's top rows) in a full-resolution frame. Needs a sharp
    step at its border; natural dark areas fade gradually."""
    out = []
    for name, lines in (("top", gray), ("bottom", gray[::-1]), ("left", gray.T), ("right", gray.T[::-1])):
        m = lines[:48].astype(np.float32).mean(1)
        ref, n = float(np.median(m[8:40])), 0
        while n < max_rows and m[n] < 0.5 * ref and ref - m[n] > 40:
            n += 1
        if n and float(np.max(m[2:n + 2] - m[:n])) >= 0.25 * ref:
            out.append(f"{n} px dark line at {name} edge")
    return out


def shot_edge_checks(video, props: dict | None, D: float) -> list[str]:
    """Start/middle/end of every shot (or one frame per second) at full resolution."""
    if props and props.get("shots"):
        stop = props.get("outro", {}).get("at", D)
        ts = [t for s in props["shots"] for a, b in [(s["at"], min(s["at"] + s["dur"], stop))] if b - a > 0.15
              for t in (a + 0.07, (a + b) / 2, b - 0.07)]
    else:
        ts = [i + 0.5 for i in range(int(D))]
    frames = sorted({min(int(D * FPS) - 1, max(0, round(t * FPS))) for t in ts if t < D})
    if not frames:
        return []
    sel = "+".join(f"eq(n\\,{n})" for n in frames)
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", f"select='{sel}'", "-fps_mode", "vfr",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    w, h = media_size(video) or (1080, 1920)
    size = w * h
    if r.returncode:
        return []
    return [f"{n / FPS:.2f} s: {p}" for i, n in enumerate(frames[:len(r.stdout) // size])
            for p in edge_lines(np.frombuffer(r.stdout, np.uint8, size, i * size).reshape(h, w))]


def _grab_at(video, t: float, size: tuple[int, int]) -> np.ndarray | None:
    w, h = size
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v",
                          "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8, w * h * 3).reshape(h, w, 3) if len(raw) == w * h * 3 else None


def drop_transient(video, notes: dict, D: float, step: float = 0.2) -> dict:
    """A finding on one sampled frame only may be a transition frame: keep it only if a +-0.2 s neighbour, checked at
    full resolution, shows the same finding."""
    size = media_size(video) or (1080, 1920)
    out = {}
    for i, found in notes.items():
        single = [f for f in found if f not in notes.get(i - 1, []) and f not in notes.get(i + 1, [])]
        keep = [f for f in found if f not in single]
        if single:
            seen = set()
            for t in (i - step, i + step):
                if 0 <= t < D and (fr := _grab_at(video, t, size)) is not None:
                    seen.update(frame_checks(fr))
            keep += [f for f in single if f in seen]
        if keep:
            out[i] = keep
    return out


def contact_sheet(frames, out, notes: dict) -> None:
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (TW * COLS, TH * ((len(frames) + COLS - 1) // COLS)), (17, 17, 17))
    s = TW / 1080
    for i, fr in enumerate(frames):
        im = Image.fromarray(fr).convert("RGBA")
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        t, b, lft, rgt = SAFE["top"] * s, SAFE["bottom"] * s, SAFE["left"] * s, SAFE["right"] * s
        for box in ([0, 0, TW, t], [0, b, TW, TH], [0, t, lft, b], [rgt, t, TW, b]):
            d.rectangle(box, fill=(255, 40, 40, 70))
        d.rectangle([lft, t, rgt, b], outline=(255, 60, 60, 220), width=1)
        im = Image.alpha_composite(im, ov).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 40, 15], fill=(0, 0, 0))
        d.text((3, 2), f"{i}s", fill=(255, 255, 0))
        if notes.get(i):
            d.rectangle([0, TH - 16, TW, TH], fill=(200, 30, 30))
            d.text((3, TH - 14), "; ".join(notes[i])[:44], fill=(255, 255, 255))
        sheet.paste(im, ((i % COLS) * TW, (i // COLS) * TH))
    sheet.save(out)


def run_qa(job: Job, force: bool = False) -> dict:
    video, rep_p = job.dir / "final.mp4", job.b("qa_report.json")
    if not video.exists():
        raise PipelineError("final.mp4 missing: run render first")
    if not force and rep_p.exists() and load_json(rep_p).get("video_sig") == file_sig(video):
        log.info("qa: video unchanged, previous report kept")
        return load_json(rep_p)
    frames = grab_frames(video)
    notes = {i: n for i, fr in enumerate(frames) if (n := frame_checks(fr))}
    D = ffprobe_duration(video)
    notes = drop_transient(video, notes, D)
    contact_sheet(frames, job.b("qa_sheet.png"), notes)
    problems = [f"{i} s: {', '.join(n)}" for i, n in sorted(notes.items())]
    fi = job.b("final_info.json")
    problems += [f"final: {p}" for p in (load_json(fi).get("problems", []) if fi.exists() else [])]
    props = load_json(job.b("props.json")) if job.b("props.json").exists() else None
    problems += shot_edge_checks(video, props, D)
    rep = {"video_sig": file_sig(video), "frames": len(frames), "sheet": "build/qa_sheet.png",
           "problems": problems, "ok": not problems}
    save_json(rep_p, rep)
    log.info(f"qa: {'ok' if rep['ok'] else f'{len(problems)} findings'} -> build/qa_sheet.png, build/qa_report.json")
    for p in problems[:10]:
        log.warning(f"  {p}")
    return rep


def record_review(job: Job, text: str) -> dict:
    text = " ".join(str(text or "").split())
    if len(text) < 10:
        raise PipelineError("--review needs a reason (why each finding is a false alarm or accepted)")
    rp = job.find("qa_report.json")
    if not rp:
        raise PipelineError("no QA report: run the qa stage first")
    rep = load_json(rp)
    final = job.dir / "final.mp4"
    if final.exists() and rep.get("video_sig") and list(rep["video_sig"]) != list(file_sig(final)):
        raise PipelineError("QA report belongs to an older final.mp4: rerun qa first")
    rep.update(reviewed=True, review=text, reviewed_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    save_json(rp, rep)
    return rep


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="show the QA report or record a review")
    ap.add_argument("job_dir")
    ap.add_argument("--review", metavar="REASON")
    a = ap.parse_args(argv)
    try:
        job = Job(a.job_dir)
        rep = record_review(job, a.review) if a.review is not None else load_json(
            job.find("qa_report.json") or job.b("qa_report.json"))
    except (PipelineError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"qa: {'ok' if rep.get('ok') else 'findings'}, reviewed: {'yes' if rep.get('reviewed') else 'no'}, "
          f"result: {'PASS' if qa_ok(rep) else 'FAIL'}")
    for p in rep.get("problems") or []:
        print(f"  - {p}")
    return 0 if qa_ok(rep) else 3


if __name__ == "__main__":
    sys.exit(main())
