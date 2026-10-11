"""job.meta -> meta.json (platform texts validated against channel limits, sources, media credits) + sources.md."""
from __future__ import annotations

import re
from datetime import datetime

from common import Job, PipelineError, beat_id, load_json, log, qa_ok, save_json

HASHTAG = re.compile(r"#\w+", re.U)
YT_DESC_MAX, YT_TAGS_CHARS = 5000, 500


def norm_tags(tags) -> list[str]:
    out = []
    for t in tags or []:
        t = "#" + re.sub(r"\W", "", str(t).lstrip("#"), flags=re.U)
        if len(t) > 1 and t not in out:
            out.append(t)
    return out


def platform_texts(meta: dict) -> dict:
    """IG/TikTok: caption plus the hashtags it does not already contain -> `text` (what gets pasted)."""
    yt = dict(meta.get("youtube") or {})
    yt["tags"] = [str(t).lstrip("#") for t in yt.get("tags", [])]
    out = {"youtube": yt}
    for name in ("instagram", "tiktok"):
        p = dict(meta.get(name) or {})
        p["hashtags"] = norm_tags(p.get("hashtags"))
        cap = str(p.get("caption", "")).rstrip()
        present = {h.lower() for h in HASHTAG.findall(cap)}
        missing = [h for h in p["hashtags"] if h.lower() not in present]
        p["text"] = (cap + ("\n\n" + " ".join(missing) if missing else "")).strip()
        out[name] = p
    return out


def validate_meta(plat: dict, channel: dict) -> tuple[list[str], list[str]]:
    E, W = [], []
    lim = channel.get("platforms") or {}
    yt, ig, tt = plat.get("youtube") or {}, plat.get("instagram") or {}, plat.get("tiktok") or {}
    title, tmax = str(yt.get("title", "")), int((lim.get("youtube") or {}).get("titleMax", 100))
    if not title:
        E.append("youtube.title is empty")
    elif len(title) > tmax:
        E.append(f"youtube.title is {len(title)} chars (max {tmax})")
    if title and not title.rstrip().endswith("#Shorts"):
        E.append("youtube.title must end with #Shorts")
    if len(yt.get("description", "")) > YT_DESC_MAX:
        E.append(f"youtube.description over {YT_DESC_MAX} chars")
    tags = yt.get("tags", [])
    if sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1) > YT_TAGS_CHARS:
        E.append(f"youtube.tags over {YT_TAGS_CHARS} chars in total")
    hmax = int((lim.get("instagram") or {}).get("hashtagsMax", 5))
    cmax = int((lim.get("tiktok") or {}).get("captionMax", 2200))
    for name, p, maxlen in (("instagram", ig, 2200), ("tiktok", tt, cmax)):
        if not p.get("caption"):
            E.append(f"{name}.caption is empty")
        if len(p.get("text", "")) > maxlen:
            E.append(f"{name} text is {len(p['text'])} chars (max {maxlen})")
        n = len({h.lower() for h in HASHTAG.findall(p.get("text", ""))})
        if name == "instagram" and n > hmax:
            E.append(f"instagram has {n} hashtags (max {hmax})")
        if n == 0:
            W.append(f"{name}: no hashtags")
    public = "\n".join([title, yt.get("description", ""), " ".join(tags), ig.get("text", ""), tt.get("text", "")])
    for phrase in channel.get("bannedPublicPhrases") or []:
        if phrase and phrase.lower() in public.lower():
            E.append(f"public text contains banned phrase '{phrase}'")
    return E, W


def collect_sources(job: Job) -> list[dict]:
    seen, out = set(), []
    for i, b in enumerate(job.beats):
        s = b.get("source") or {}
        k = (s.get("name"), s.get("date"), s.get("url"))
        if s.get("name") and k not in seen:
            seen.add(k)
            out.append({"beat": beat_id(b, i), "name": s["name"], "date": s.get("date"), "url": s.get("url")})
    return out


def collect_media(job: Job) -> list[dict]:
    """Catalog entries actually used in the edit (live files, else keep/ copies)."""
    cp, pp = job.find("catalog.json"), job.find("props.json")
    cat = load_json(cp) if cp else []
    cat = (cat.get("items") or []) if isinstance(cat, dict) else cat
    srcs = {s["src"] for s in load_json(pp)["shots"]} if pp else None
    used = [c for c in cat if srcs is None or c.get("file", "").replace("\\", "/") in srcs
            or any(s.startswith(f"clips/_zoom/{c.get('id')}__") for s in srcs)]
    return [{k: c[k] for k in ("id", "file", "kind", "srcUrl", "srcStart", "srcEnd", "rights", "credit",
                               "illustrative") if c.get(k) not in (None, "")} for c in used]


def build_meta(job: Job) -> tuple[dict, list[str], list[str]]:
    """Pure: no files written. On a cleaned job the old sources/media/video/production are kept."""
    d, mp = job.data, job.dir / "meta.json"
    old = load_json(mp) if mp.exists() else {}
    plat = platform_texts(d.get("meta") or {})
    errors, warns = validate_meta(plat, job.channel)
    if old and not job.assets.exists():
        keep = {k: old.get(k) for k in ("sources", "media", "video", "production")}
    else:
        fi, tp, qp = job.find("final_info.json"), job.find("tts_info.json"), job.find("qa_report.json")
        f = load_json(fi) if fi else {}
        t = load_json(tp) if tp else {}
        q = load_json(qp) if qp else None
        keep = {"sources": collect_sources(job), "media": collect_media(job),
                "video": {"file": "final.mp4", "durationSec": round(f.get("duration", 0), 2), "lufs": f.get("lufs"),
                          "sizeBytes": f.get("size")} if f else {},
                "production": {"generated": datetime.now().astimezone().isoformat(timespec="seconds"),
                               "tts": {k: t.get(k) for k in ("model", "voice", "style", "tempo", "duration")},
                               "qa": {"ok": qa_ok(q), "problems": (q or {}).get("problems", [])[:10],
                                      "review": (q or {}).get("review")} if q else None}}
    meta = {"slug": d.get("slug"), "date": d.get("date"),
            "hook": " ".join(str(d.get("hook", {}).get("title", "")).replace("*", "").split()), **plat, **keep}
    return meta, errors, warns


def write_sources_md(job: Job, meta: dict) -> None:
    L = [f"# Sources: {meta['slug']} ({meta['date']})", "", "## Facts"]
    L += [f"- {s['name']}, {s.get('date') or '?'}: {s.get('url') or 'no url'} ({s['beat']})"
          for s in meta.get("sources") or []] or ["- none"]
    L += ["", "## Footage"]
    L += [f"- {m.get('id')}: {m.get('credit') or '-'} / rights: {m.get('rights') or '?'}"
          f"{' / illustrative' if m.get('illustrative') else ''}: {m.get('srcUrl') or 'no url'}"
          for m in meta.get("media") or []] or ["- none"]
    (job.dir / "sources.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def run_meta(job: Job) -> dict:
    meta, errors, warns = build_meta(job)
    for w in warns:
        log.warning(f"meta: {w}")
    if errors:
        raise PipelineError("platform texts invalid:\n  " + "\n  ".join(errors))
    save_json(job.dir / "meta.json", meta)
    write_sources_md(job, meta)
    log.info(f"meta: meta.json + sources.md ({len(meta['sources'] or [])} sources, {len(meta['media'] or [])} media)")
    return meta
