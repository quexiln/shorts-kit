"""job.json validation, word references (cues, until, quotes) and framing limits.
validate_job returns (errors, warnings); errors stop the pipeline (exit 2)."""
from __future__ import annotations

import difflib
import re

from common import TTS_TAG, TTS_TAGS_KNOWN, beat_id, expand_spoken, norm, parse_text

ZOOM_MAX = 1.12        # fixed zoom limit
UPSCALE_OK = 2.0       # cover x zoom above this: warning without zoom, error with zoom
MIN_SIDE = 720         # sources below 720p are rejected
KNOWN_TOP = {"slug", "date", "hook", "beats", "tts", "meta", "edit", "status", "notes"}
KNOWN_BEAT = {"id", "text", "source", "visual", "overlay"}
STAT_KEYS = {"type", "title", "unit", "items", "until"}
QUOTE_KEYS = {"type", "quote", "name", "role", "highlight", "sourceText", "until"}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def find_cue(words: list[dict], cue, lang: str = "en") -> int | None:
    """Index of the word where `cue` (one or more words) starts; exact match first, then prefix."""
    q = [n for n in (norm(x, lang) for x in str(cue).split()) if n]
    ws = [norm(w["text"], lang) for w in words]
    for exact in (True, False):
        for i in range(len(ws) - len(q) + 1):
            if q and all((ws[i + j] == q[j]) if exact else ws[i + j].startswith(q[j]) for j in range(len(q))):
                return i
    return None


def _qwords(text: str, lang: str) -> list[str]:
    return [n for tok in str(text).split() for p in expand_spoken(tok, lang) for x in p.split()
            for n in [norm(x, lang)] if n]


def quote_in_source(quote: str, source_text: str, lang: str = "en") -> bool:
    """Quote appears contiguously, in order, in the source text (case/punctuation/number spelling normalised)."""
    q, s = _qwords(quote, lang), _qwords(source_text, lang)
    return bool(q) and any(s[i:i + len(q)] == q for i in range(len(s) - len(q) + 1))


def alias_errors(bid: str, text: str, lang: str) -> list[str]:
    """'New York|new_york' would speak 'New' twice: a multi-word written side must be joined with '_'."""
    E, raw = [], TTS_TAG.sub(" ", text).split()
    for i, tok in enumerate(raw):
        if "|" not in tok or i == 0 or "|" in raw[i - 1]:
            continue
        disp, tts = tok.replace("*", "").split("|", 1)
        prev, head = norm(raw[i - 1].replace("*", ""), lang), norm((tts.replace("_", " ").split() or [""])[0], lang)
        if len(prev) >= 3 and head and max(difflib.SequenceMatcher(None, prev, head).ratio(),
                                           difflib.SequenceMatcher(None, prev, norm(tts, lang)[:len(prev)]).ratio()) >= 0.75:
            E.append(f"{bid}: '{raw[i - 1]} {tok}': write the multi-word name as one unit "
                     f"'{raw[i - 1].replace('*', '')}_{disp}|{tts}'")
    return E


def validate_job(data: dict, lang: str = "en", closing_line: str | None = None) -> tuple[list[str], list[str]]:
    E, W = [], []
    for k in ("slug", "date", "hook", "beats"):
        if k not in data:
            E.append(f"missing field '{k}'")
    if E:
        return E, W
    W += [f"unknown field '{k}'" for k in data if k not in KNOWN_TOP and not str(k).startswith("_")]
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(data["slug"])):
        E.append(f"slug must be kebab-case: {data['slug']}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data["date"])):
        E.append(f"date must be YYYY-MM-DD: {data['date']}")
    hook = data["hook"] if isinstance(data["hook"], dict) else {}
    if not hook.get("title"):
        E.append("hook.title is empty")
    u = hook.get("until")
    if u is not None and not (_num(u) or isinstance(u, str)):
        E.append("hook.until must be \"sentence\", a word or seconds")
    beats = data["beats"] if isinstance(data["beats"], list) else []
    if not beats:
        E.append("beats is empty")
    if "meta" not in data:
        W.append("no meta (platform texts); the meta stage will fail")
    ids = set()
    for i, b in enumerate(beats):
        bid = beat_id(b, i)
        if bid in ids:
            E.append(f"duplicate beat id {bid}")
        ids.add(bid)
        W += [f"{bid}: unknown field '{k}'" for k in b if k not in KNOWN_BEAT]
        text = b.get("text", "")
        words = [{"text": t["display"]} for t in parse_text(text)]
        if not words:
            E.append(f"{bid}: text is empty")
        E += alias_errors(bid, text, lang)
        W += [f"{bid}: unknown TTS tag <{m.group(1)}>" for m in TTS_TAG.finditer(text)
              if " ".join(m.group(1).lower().split()) not in TTS_TAGS_KNOWN]
        src = b.get("source")
        if src is not None:
            if not isinstance(src, dict) or not src.get("name"):
                E.append(f"{bid}: source needs at least a name")
            elif not src.get("date") or not src.get("url"):
                W.append(f"{bid}: source without date or url ({src.get('name')})")
        vis = b.get("visual") or {}
        if not isinstance(vis.get("assets", []), list):
            E.append(f"{bid}: visual.assets must be a list")
        cues = vis.get("cues") or {}
        if not isinstance(cues, dict):
            E.append(f"{bid}: visual.cues must be an object {{asset id: word}}")
            cues = {}
        for aid, cue in cues.items():
            if aid not in (vis.get("assets") or []):
                W.append(f"{bid}: cue asset '{aid}' not in visual.assets")
            if find_cue(words, cue, lang) is None:
                W.append(f"{bid}: cue word '{cue}' not in the beat text")
        ov = b.get("overlay")
        if ov:
            E += [f"{bid}: {e}" for e in check_overlay(ov, words, lang, src)]
    if closing_line and beats:
        last = [norm(t["display"], lang) for t in parse_text(beats[-1].get("text", ""))]
        want = [n for n in (norm(x, lang) for x in closing_line.split()) if n]
        if last[-len(want):] != want:
            W.append(f"last beat does not end with the closing line '{closing_line}'")
    return E, W


def check_overlay(ov: dict, words: list[dict], lang: str, src) -> list[str]:
    E, t = [], ov.get("type")
    if t == "stat":
        E += [f"stat: unknown field '{k}'" for k in ov if k not in STAT_KEYS]
        items = ov.get("items")
        if not ov.get("title"):
            E.append("stat: title is empty")
        if not isinstance(items, list) or not items or not all(
                isinstance(x, dict) and x.get("label") is not None and _num(x.get("value")) for x in items):
            E.append("stat: items must be [{label, value: number, highlight?}]")
    elif t == "quote":
        E += [f"quote: unknown field '{k}'" for k in ov if k not in QUOTE_KEYS]
        for k in ("quote", "name", "sourceText"):
            if not ov.get(k):
                E.append(f"quote: '{k}' is empty")
        if ov.get("quote") and ov.get("sourceText") and not quote_in_source(ov["quote"], ov["sourceText"], lang):
            E.append("quote: not found verbatim in sourceText")
        if ov.get("highlight") and ov["highlight"] not in (ov.get("quote") or ""):
            E.append("quote: highlight is not part of the quote")
        if not src:
            E.append("quote: beat has no source")
    else:
        E.append("overlay.type must be stat or quote")
    if ov.get("until") and find_cue(words, ov["until"], lang) is None:
        E.append(f"overlay.until word '{ov['until']}' not in the beat text")
    return E


# ------------------------------------------------------------------ framing
def static_zoom(c: dict) -> float:
    z = c.get("zoom")
    return float(z) if _num(z) and z > 1.001 else 1.0


def upscale_factor(size: tuple[int, int], zoom: float = 1.0) -> float:
    """Cover-crop scale from source to 1080x1920, times fixed zoom (1080p landscape -> 1.78)."""
    return max(1080 / size[0], 1920 / size[1]) * zoom


def check_framing(items: list[dict], size_of) -> tuple[list[str], list[str]]:
    E, W = [], []
    for c in items:
        cid, z = c.get("id"), static_zoom(c)
        if z > ZOOM_MAX + 1e-6:
            E.append(f"{cid}: zoom {z} > {ZOOM_MAX}; choose another moment or source instead of zooming")
        size = size_of(c)
        if not size:
            continue
        up, res = upscale_factor(size, z), f"{size[0]}x{size[1]}"
        if min(size) < MIN_SIDE:
            E.append(f"{cid}: {res} is below 720p")
        elif up > UPSCALE_OK + 1e-6 and z > 1.001:
            E.append(f"{cid}: {res} upscales {up / z:.2f}x; zoom on top is not allowed (720p only without zoom)")
        elif up > UPSCALE_OK + 1e-6:
            W.append(f"{cid}: {res} upscales {up:.2f}x (soft); prefer a 1080p source")
    return E, W
