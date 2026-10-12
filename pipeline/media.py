"""Footage tools.
  python pipeline/media.py get <url> --job <job_dir> --start S --end E --rights R --credit C [--illustrative]
                               [--id ID] [--image]
  python pipeline/media.py sheet <job_dir> [--id ID] [--fx F] [--fy F] [--zoom Z] [--from A] [--to B]
  python pipeline/media.py check <job_dir>
get   downloads only the time range (yt-dlp <= 1080p; direct .mp4/.mov/.webm URLs via ffmpeg) to assets/video/<id>.mp4 (or an image to assets/img/) and
      upserts assets/catalog.json. S/E: seconds or MM:SS of the source video. Catalog `from`/`to` are FILE times
      (pick them from the sheet); focusX/focusY/zoom are set by hand.
sheet build/sheets/<id>.png: timestamped frames with the 9:16 crop window (outside dimmed) and a focusX ruler.
check every catalog item: file, resolution, upscale, zoom, from/to, focus, rights/credit; links to job.json."""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from checks import UPSCALE_OK, check_framing, static_zoom, upscale_factor
from common import Job, PipelineError, beat_id, ffprobe_duration, media_size, save_json

SECTION_MAX = 120.0
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
YTDLP_FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba/bv*[height<=1080]+ba/b[height<=1080]/b"
VIDEO_EXT, IMAGE_EXT = (".mp4", ".mov", ".webm", ".m4v"), {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".webp": "webp"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}")


def parse_time(s) -> float:
    parts = str(s).strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise PipelineError(f"bad time '{s}' (seconds or MM:SS)")
    t = 0.0
    for p in parts:
        t = t * 60 + float(p)
    return t


def crop_window(W: int, H: int, fx=0.5, fy=0.5, zoom=1.0) -> tuple[float, float, float, float]:
    """Source pixels shown by the 9:16 cover crop: (x0, y0, w, h)."""
    s = max(1080 / W, 1920 / H) * zoom
    w, h = 1080 / s, 1920 / s
    return (W - w) * fx, (H - h) * fy, w, h


def upsert(job: Job, rec: dict) -> dict:
    cat = job.catalog()
    cur = next((c for c in cat if c.get("id") == rec["id"]), None)
    if cur is None:
        cur = {"id": rec["id"]}
        cat.append(cur)
    cur.update({k: v for k, v in rec.items() if v is not None})
    save_json(job.a("catalog.json"), cat)
    return cur


def next_id(job: Job, image: bool) -> str:
    ids = {c.get("id") for c in job.catalog()}
    p = "p" if image else "v"
    return next(f"{p}{i:02d}" for i in range(1, 1000) if f"{p}{i:02d}" not in ids)


def _ext(url: str) -> str:
    return Path(urllib.parse.urlparse(url).path).suffix.lower()


def get(job: Job, url: str, start=None, end=None, rights=None, credit=None, illustrative=False, id_=None,
        image=False) -> dict:
    image = image or _ext(url) in IMAGE_EXT
    id_ = id_ or next_id(job, image)
    if not _ID.fullmatch(id_):
        raise PipelineError(f"bad id '{id_}'")
    if not re.match(r"https?://", url):
        raise PipelineError("url must be http(s)")
    if image:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (shorts-kit)"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (http(s) only, type checked)
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in IMAGE_TYPES and not (_ext(url) in IMAGE_EXT and ctype in ("", "application/octet-stream")):
                raise PipelineError(f"not an image ({ctype or '?'})")
            data = r.read(30_000_001)
        if len(data) > 30_000_000:
            raise PipelineError("image larger than 30 MB")
        f = job.a("img", f"{id_}.{IMAGE_TYPES.get(ctype) or IMAGE_EXT[_ext(url)]}")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        rec = {"id": id_, "file": f"img/{f.name}", "kind": "image", "srcUrl": url}
    else:
        if start is None or end is None:
            raise PipelineError("video needs --start and --end (or use --image)")
        a, b = parse_time(start), parse_time(end)
        if not 0 < b - a <= SECTION_MAX:
            raise PipelineError(f"range must be 0-{SECTION_MAX:.0f} s")
        out = job.a("video")
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob(f"{id_}.*"):
            old.unlink()
        if _ext(url) in VIDEO_EXT:  # direct file: yt-dlp reports no height for these, so cut with ffmpeg
            f = out / f"{id_}.mp4"
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", url,
                                "-an", "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
                                "-movflags", "+faststart", str(f)], capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode or not f.exists():
                raise PipelineError(f"ffmpeg failed:\n{r.stderr[-1200:]}")
            files = [f]
        else:
            exe = [shutil.which("yt-dlp")] if shutil.which("yt-dlp") else [sys.executable, "-m", "yt_dlp"]
            r = subprocess.run(exe + ["--no-playlist", "--no-progress", "-f", YTDLP_FORMAT, "--merge-output-format",
                                      "mp4", "--download-sections", f"*{a:.2f}-{b:.2f}", "-o",
                                      str(out / f"{id_}.%(ext)s"), url],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            files = [f for f in sorted(out.glob(f"{id_}.*")) if not f.name.endswith((".part", ".ytdl"))]
            if r.returncode or not files:
                raise PipelineError(f"yt-dlp failed:\n{(r.stderr or r.stdout)[-1200:]}")
        f = files[0]
        rec = {"id": id_, "file": f"video/{f.name}", "kind": "video", "srcUrl": url, "srcStart": round(a, 2),
               "srcEnd": round(b, 2)}
    size = media_size(f)
    rec.update(width=size and size[0], height=size and size[1], rights=rights, credit=credit,
               illustrative=bool(illustrative))
    cur = upsert(job, rec)
    up = upscale_factor(size, static_zoom(cur)) if size else 0
    print(f"{id_}: {rec['file']} {size[0] if size else '?'}x{size[1] if size else '?'}, upscale {up:.2f}x"
          + (" (soft, prefer 1080p)" if up > UPSCALE_OK else ""))
    return cur


# ------------------------------------------------------------------ sheet
def _frame(path: Path, t, w: int, h: int):
    from PIL import Image
    if t is None:
        with Image.open(path) as im:
            return im.convert("RGB").resize((w, h))
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf",
                          f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return Image.frombytes("RGB", (w, h), raw) if len(raw) == w * h * 3 else Image.new("RGB", (w, h), (60, 0, 0))


def _guides(im, W, H, fx, fy, zoom, label):
    from PIL import Image, ImageDraw
    tw, th = im.size
    sx, sy = tw / W, th / H
    x0, y0, w, h = crop_window(W, H, fx, fy, zoom)
    X0, Y0, X1, Y1 = x0 * sx, y0 * sy, (x0 + w) * sx, (y0 + h) * sy
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for box in ([0, 0, tw, Y0], [0, Y1, tw, th], [0, Y0, X0, Y1], [X1, Y0, tw, Y1]):
        d.rectangle(box, fill=(0, 0, 0, 130))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle([X0, Y0, X1 - 1, Y1 - 1], outline=(255, 210, 0), width=2)
    if W - w > 1:  # focusX ruler: which tick sits under the subject
        for k in range(11):
            cx = ((W - w) * k / 10 + w / 2) * sx
            d.line([(cx, th - (9 if k % 5 == 0 else 5)), (cx, th)], fill=(255, 210, 0))
    d.rectangle([0, 0, 8 + 6 * len(label), 15], fill=(0, 0, 0))
    d.text((3, 2), label, fill=(255, 255, 0))
    return im


def make_sheet(job: Job, c: dict, fx=None, fy=None, zoom=None, t0=None, t1=None) -> Path:
    from PIL import Image, ImageDraw
    path = job.a(c["file"])
    size = media_size(path)
    if not size:
        raise PipelineError(f"{c['id']}: cannot read {c['file']}")
    W, H = size
    fx, fy = float(c.get("focusX", 0.5) if fx is None else fx), float(c.get("focusY", 0.5) if fy is None else fy)
    z = static_zoom(c) if zoom is None else max(1.0, float(zoom))
    if c.get("kind") == "image":
        times, tw = [None], 960 if W >= H else int(960 * W / H)
    else:
        D = ffprobe_duration(path)
        lo = float(t0) if t0 is not None else max(0.0, float(c.get("from") or 0) - (1 if "from" in c else 0))
        hi = min(float(t1) if t1 is not None else (float(c["to"]) + 1 if c.get("to") is not None else D), D - 0.05)
        st = max(0.25, round((hi - lo) / 23, 2))
        times = [round(lo + i * st, 2) for i in range(min(48, int((hi - lo) / st) + 1))]
        tw = 480 if W >= H else int(480 * W / H)
    th = int(round(tw * H / W / 2)) * 2
    tiles = [_guides(_frame(path, t, tw, th), W, H, fx, fy, z, c["file"] if t is None else f"{t:.2f}") for t in times]
    cols = 1 if times == [None] else (4 if W >= H else 8)
    sheet = Image.new("RGB", (tw * cols, th * ((len(tiles) + cols - 1) // cols) + 22), (17, 17, 17))
    ImageDraw.Draw(sheet).text((6, 5), f"{c['id']} {W}x{H} upscale {upscale_factor((W, H), z):.2f}x "
                                       f"focusX {fx:g} focusY {fy:g} zoom {z:g} (file time)", fill=(255, 210, 0))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * tw, 22 + (i // cols) * th))
    out = job.b("sheets", f"{c['id']}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


# ------------------------------------------------------------------ check
def check_catalog(job: Job) -> tuple[list[str], list[str], list[dict]]:
    E, W = [], []
    cat = job.catalog()
    ids = [c.get("id") for c in cat]
    E += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    sizes = {}
    for c in cat:
        cid = c.get("id") or "?"
        if c.get("kind", "video") not in ("video", "image"):
            E.append(f"{cid}: kind must be video or image")
        for k in ("srcUrl", "rights", "credit"):
            if not c.get(k):
                E.append(f"{cid}: '{k}' is empty")
        if not isinstance(c.get("illustrative", False), bool):
            E.append(f"{cid}: illustrative must be true/false")
        z = c.get("zoom")
        if z is not None and not isinstance(z, (int, float)) and z not in ("none", "punch", "slow"):
            E.append(f"{cid}: zoom must be a number (<= 1.12) or none|punch|slow")
        for k in ("focusX", "focusY"):
            if c.get(k) is not None and not (isinstance(c[k], (int, float)) and 0 <= c[k] <= 1):
                E.append(f"{cid}: {k} must be 0-1")
        path = job.a(c.get("file") or "-")
        if not c.get("file") or not path.exists():
            E.append(f"{cid}: file missing (assets/{c.get('file')})")
            continue
        sizes[cid] = media_size(path)
        if c.get("kind", "video") == "video" and c.get("from") is not None and c.get("to") is not None:
            if float(c["to"]) <= float(c["from"]):
                E.append(f"{cid}: to <= from")
            elif float(c["to"]) > ffprobe_duration(path) + 0.05:
                E.append(f"{cid}: to is past the end of the file")
    fe, fw = check_framing([c for c in cat if c.get("id") in sizes], lambda c: sizes.get(c.get("id")))
    known = set(ids)
    for i, b in enumerate(job.beats):
        E += [f"{beat_id(b, i)}: asset '{a}' not in catalog" for a in (b.get("visual") or {}).get("assets") or []
              if a not in known]
    return E + fe, W + fw, cat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="footage tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("url")
    g.add_argument("--job", required=True)
    g.add_argument("--start")
    g.add_argument("--end")
    g.add_argument("--rights")
    g.add_argument("--credit")
    g.add_argument("--illustrative", action="store_true")
    g.add_argument("--id")
    g.add_argument("--image", action="store_true")
    s = sub.add_parser("sheet")
    s.add_argument("job_dir")
    s.add_argument("--id")
    for k in ("--fx", "--fy", "--zoom"):
        s.add_argument(k, type=float)
    s.add_argument("--from", dest="t0", type=float)
    s.add_argument("--to", dest="t1", type=float)
    c = sub.add_parser("check")
    c.add_argument("job_dir")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "get":
            get(Job(a.job), a.url, a.start, a.end, a.rights, a.credit, a.illustrative, a.id, a.image)
            return 0
        job = Job(a.job_dir)
        if a.cmd == "sheet":
            items = [x for x in job.catalog() if not a.id or x.get("id") == a.id]
            if not items:
                raise PipelineError("nothing to sheet (catalog empty or unknown --id)")
            for x in items:
                print(make_sheet(job, x, a.fx, a.fy, a.zoom, a.t0, a.t1))
            return 0
        E, W, cat = check_catalog(job)
        for w in W:
            print(f"WARN {w}")
        for e in E:
            print(f"ERROR {e}")
        print(f"media check: {len(cat)} items, {len(E)} errors, {len(W)} warnings")
        return 1 if E else 0
    except (PipelineError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
