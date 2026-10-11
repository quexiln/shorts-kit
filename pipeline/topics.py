"""Past jobs in jobs/<date>/<slug>/ so topics are not repeated.
  python pipeline/topics.py [--days 14] [--all] [--q WORD] [--json]"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from common import ROOT, load_json, norm


def list_topics(days: int | None = 14, jobs_dir=ROOT / "jobs", q: str | None = None, today: date | None = None):
    since = (today or date.today()) - timedelta(days=days) if days is not None else None
    out = []
    for jp in sorted(Path(jobs_dir).glob("*/*/job.json")):
        try:
            d = load_json(jp)
            dt = date.fromisoformat(str(d.get("date") or jp.parent.parent.name))
        except (OSError, ValueError):
            continue
        if since and dt < since:
            continue
        jd = jp.parent
        title = " ".join(str((d.get("hook") or {}).get("title", "")).replace("*", "").split())
        text = " ".join(str(b.get("text", "")) for b in d.get("beats") or [])
        if q and norm(q) not in norm(" ".join([d.get("slug", ""), title, text])):
            continue
        state = ("cleaned" if not (jd / "assets").exists() else "ready") if (jd / "final.mp4").exists() else \
            "in progress"
        out.append({"date": dt.isoformat(), "slug": d.get("slug") or jd.name, "title": title,
                    "status": d.get("status") or state, "dir": str(jd.relative_to(Path(jobs_dir).parent))})
    return sorted(out, key=lambda r: (r["date"], r["slug"]), reverse=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="list recent jobs")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--q")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = list_topics(None if a.all else a.days, q=a.q)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    for r in rows:
        print(f"{r['date']}  {r['slug']:<32} {r['status']:<12} {r['title']}")
    print(f"{len(rows)} jobs" + ("" if a.all else f" in the last {a.days} days"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
