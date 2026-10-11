"""Remotion render (remotion/) -> build/render.mp4, then two-pass loudnorm to -14 LUFS -> final.mp4."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from common import FPS, REMOTION, Job, PipelineError, ceil_frames, ffprobe_streams, file_sig, load_json, log, run, \
    save_json, sha1

COMPOSITION = "Video"
TARGET_LUFS = -14.0


def remotion_cmd(job: Job, out, props) -> list[str]:
    """node + remotion-cli.js when installed (avoids npx quoting issues on Windows), else npx."""
    cli = REMOTION / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    node = shutil.which("node")
    if cli.exists() and node:
        base = [node, str(cli)]
    else:
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            raise PipelineError("node/npx not found")
        base = [npx, "remotion"]
    return base + ["render", "src/index.ts", COMPOSITION, str(out), f"--props={props}", f"--public-dir={job.assets}",
                   "--crf=20", "--timeout=120000"]


def render_remotion(job: Job) -> None:
    if not (REMOTION / "node_modules").exists():
        raise PipelineError("remotion/node_modules missing: run `npm install` in remotion/")
    t0 = time.time()
    p = subprocess.Popen(remotion_cmd(job, job.b("render.mp4"), job.b("props.json")), cwd=REMOTION,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace")
    tail = []
    with open(job.b("remotion.log"), "w", encoding="utf-8") as lf:
        for line in p.stdout:
            lf.write(line)
            tail = (tail + [line.rstrip()])[-25:]
    if p.wait():
        raise PipelineError("remotion render failed (build/remotion.log):\n" + "\n".join(tail))
    log.info(f"render: remotion done in {time.time() - t0:.0f} s")


def loudness(path) -> dict | None:
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
             f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    return json.loads(m.group(0)) if m else None


def finalize(src, out) -> dict:
    """Two-pass loudnorm to -14 LUFS, AAC 192k, video stream copied, faststart."""
    m = loudness(src)
    ok = m and m.get("input_i") not in (None, "-inf") and float(m["input_i"]) > -70
    af = f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11" + (
        f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true" if ok else "") + \
        ",aresample=48000"
    tmp = str(out) + ".tmp.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
         "-af", af, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", tmp])
    os.replace(tmp, out)
    after = loudness(out)
    return {"lufs": float(after["input_i"]) if after and after.get("input_i") not in (None, "-inf") else None}


def probe_video(path) -> dict:
    info = ffprobe_streams(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
    return {"width": v.get("width"), "height": v.get("height"), "fps": round(int(num) / max(1, int(den)), 3),
            "audio": any(s["codec_type"] == "audio" for s in info["streams"]),
            "duration": float(info["format"]["duration"]), "size": int(info["format"].get("size", 0))}


def run_render(job: Job, force: bool = False) -> dict:
    if not job.b("props.json").exists():
        raise PipelineError("build/props.json missing: run timeline first")
    props = load_json(job.b("props.json"))
    files = sorted({props["voice"]["src"], *(s["src"] for s in props["shots"]),
                    *(s["src"] for s in props.get("sfx", []))} | ({props["music"]["src"]} if props.get("music") else set()))
    sig = sha1(props, [file_sig(job.a(f)) for f in files], file_sig(REMOTION / "src"))
    info_p, rmp4, final = job.b("render_info.json"), job.b("render.mp4"), job.dir / "final.mp4"
    if rmp4.exists() and info_p.exists() and not force and load_json(info_p).get("sig") == sig:
        log.info("render: inputs unchanged, remotion skipped")
    else:
        render_remotion(job)
        save_json(info_p, {"sig": sig})
    res = dict(finalize(rmp4, final), **probe_video(final))
    want = ceil_frames(props["durationSec"]) / FPS
    res["problems"] = [p for p, bad in (
        (f"resolution {res['width']}x{res['height']}", (res["width"], res["height"]) != (1080, 1920)),
        (f"fps {res['fps']}", abs(res["fps"] - FPS) > 0.01),
        ("no audio", not res["audio"]),
        (f"duration {res['duration']:.2f} s (expected {want:.2f})", abs(res["duration"] - want) > 0.2),
        (f"loudness {res['lufs']} LUFS", res["lufs"] is not None and abs(res["lufs"] - TARGET_LUFS) > 1.0)) if bad]
    save_json(job.b("final_info.json"), res)
    log.info(f"render: final.mp4 {res['duration']:.2f} s, {res['lufs'] or 0:.1f} LUFS"
             + (f", problems: {'; '.join(res['problems'])}" if res["problems"] else ""))
    return res
