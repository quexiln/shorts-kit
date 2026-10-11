"""Create a demo job: jobs/<today>/library-hours/ with job.json, synthetic footage and assets/catalog.json.
  python examples/demo/setup_demo.py [--force]
Footage is generated with ffmpeg test sources (no real people, places or brands). Nothing is written to examples/."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CLIPS = [  # id, lavfi source, illustrative
    ("v01", "testsrc2=size=1920x1080:rate=30:duration=6", False),
    ("v02", "gradients=size=1920x1080:rate=30:duration=6:speed=0.02:c0=0x1b2a49:c1=0xe0a030", False),
    ("v03", "testsrc2=size=1920x1080:rate=30:duration=6,hue=h=120", False),
    ("v04", "gradients=size=1920x1080:rate=30:duration=6:speed=0.03:c0=0x204030:c1=0x80c0a0:type=radial", True),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="set up the demo job")
    ap.add_argument("--force", action="store_true", help="overwrite an existing demo job")
    a = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found", file=sys.stderr)
        return 1
    today = date.today().isoformat()
    job = ROOT / "jobs" / today / "library-hours"
    if job.exists():
        if not a.force:
            print(f"ERROR: {job} exists (use --force to recreate)", file=sys.stderr)
            return 1
        shutil.rmtree(job)
    (job / "assets" / "video").mkdir(parents=True)
    data = json.loads((HERE / "job.json").read_text(encoding="utf-8"))
    data["date"] = today
    (job / "job.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    catalog = []
    for cid, src, illus in CLIPS:
        out = job / "assets" / "video" / f"{cid}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(out)], check=True)
        catalog.append({"id": cid, "file": f"video/{cid}.mp4", "kind": "video", "srcUrl": "https://example.com/demo",
                        "srcStart": 0, "srcEnd": 6, "width": 1920, "height": 1080, "from": 0.2, "to": 5.8,
                        "rights": "Synthetic test pattern (generated)", "credit": "Demo footage",
                        "illustrative": illus, "focusX": 0.5, "focusY": 0.5})
    (job / "assets" / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n", encoding="utf-8")
    rel = job.relative_to(ROOT).as_posix()
    print(f"demo job ready: {rel}\nnext:\n"
          f"  python pipeline/make_video.py {rel} --check\n"
          f"  python pipeline/make_video.py {rel} --to meta                      # needs GEMINI_API_KEY\n"
          f"  python pipeline/make_video.py {rel} --to meta --voice your.wav     # or your own narration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
