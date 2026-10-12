"""job.json + assets/catalog.json + build/words.json -> build/props.json (contract: remotion/src/schema.ts).
- Shots sit on beat windows; long beats are split into 1.2-2.5 s shots at word boundaries (visual.cues anchor an
  asset to a word). All cuts are on the 30 fps frame grid, contiguous, no gaps; a source a few frames short holds
  its last frame (<= 0.25 s). A still holds up to 4 s (slow zoom) rather than ping-pong with another asset; no
  footage range is shown twice while fresh material exists; shots under 1 s merge into a neighbour.
- Source tag per beat with a source, stat/quote cards (captions off meanwhile), hook, outro at the closing line."""
from __future__ import annotations

import math
import re

from checks import check_framing, find_cue, static_zoom
from common import FPS, Job, PipelineError, beat_id, ceil_frames, expand_spoken, ffprobe_duration, load_json, log, \
    media_size, norm, run, save_json

SHOT_MIN, SHOT_MAX, SHOT_TARGET = 1.2, 2.5, 1.8
CUT_LEAD = 0.08        # beat cut this long before its first word
WORD_CUT_LEAD = 0.04   # in-beat cut this long before a word
HOLD_MAX = 0.25        # source short by up to this: last frame is held
MIN_PIECE = 0.5
IMG_MAX = 4.0          # a still holds this long at most before another asset is needed
SHOT_FLOOR = 1.0       # shorter shots merge into a neighbour
OUTRO_SEC, OUTRO_GAP = 2.5, 0.05
CARD_MIN, CARD_MAX = 1.5, 4.5
SOURCE_MIN, SOURCE_MAX, TAG_SPILL, TAG_MIN = 1.6, 3.2, 1.0, 1.4
HOOK_MIN, HOOK_MAX, HOOK_DUP = 1.8, 4.0, 0.6
QUOTE_LEAD, QUOTE_PRE_MIN = 0.1, 1.2
CUE_PRE_MIN, CUE_SHOT_MAX, CUE_EARLY = 0.8, 2.8, 0.4
PUNCH_PEAK = 1.08
MUSIC_VOL = 0.16

TOP_KEYS = {"durationSec", "theme", "voice", "music", "sfx", "words", "captionsOff", "hook", "shots", "overlays",
            "outro"}
REQUIRED_KEYS = TOP_KEYS - {"music", "sfx", "captionsOff"}
THEME_KEYS = {"accent", "text", "dark", "font", "channelName", "handle", "illustrativeLabel"}
SHOT_KEYS = {"src", "kind", "at", "dur", "from", "focusX", "focusY", "zoom", "illustrative", "credit"}
OVERLAY_KEYS = {"source": {"type", "at", "dur", "source", "date"},
                "stat": {"type", "at", "dur", "title", "unit", "items"},
                "quote": {"type", "at", "dur", "quote", "name", "role", "highlight"}}


def _f(t: float) -> int:
    return int(round(t * FPS))


def _s(f: int) -> float:
    return round(f / FPS, 4)


def _r(t: float) -> float:
    return round(t, 3)


# ------------------------------------------------------------------ shot splitting
def split_window(fa: int, fb: int, wlist, smin=SHOT_MIN, smax=SHOT_MAX, target=SHOT_TARGET, caps=None) -> list[int]:
    """Split frames [fa, fb) into n shots at word boundaries. Returns frame bounds [fa, ..., fb].
    caps: usable seconds of each asset in order; if given, n = len(caps) and pieces follow their lengths."""
    D = (fb - fa) / FPS
    n = max(1, round(D / target))
    while n > 1 and D / n < smin:
        n -= 1
    while D / n > smax:
        n += 1
    if caps and len(caps) >= 2 and sum(caps) + 1e-6 >= D and D / len(caps) >= smin * 0.75:
        n = len(caps)
    else:
        caps = None
    if n == 1:
        return [fa, fb]
    cands = []
    for k in range(1, len(wlist)):
        w, p = wlist[k], wlist[k - 1]
        c = min(_f(max(p["end"], w["start"] - WORD_CUT_LEAD)), _f(w["start"]))
        if fa < c < fb:
            cands.append((c, bool(w.get("key"))))
    speech = [(_f(w["start"]), _f(w["end"])) for w in wlist]
    bounds = [fa]
    for k in range(1, n):
        prev, left = bounds[-1], n - k
        ideal = fa + (fb - fa) * (sum(caps[:k]) / sum(caps) if caps else k / n)
        chosen = None
        for relax in (1.0, 0.8):
            lo = max(prev + _f(smin * relax), fb - _f(smax / relax) * left)
            hi = min(fb - _f(smin * relax) * left, prev + _f(smax / relax))
            if caps:
                lo_c, hi_c = max(lo, fb - _f(sum(caps[k:]))), min(hi, prev + _f(caps[k - 1]))
                if lo_c <= hi_c:
                    lo, hi = lo_c, hi_c
            ok = [(c, key) for c, key in cands if lo <= c <= hi]
            if ok:  # nearest word boundary, key words preferred
                chosen = min(ok, key=lambda x: abs(x[0] - ideal) - (0.25 * FPS if x[1] else 0))[0]
                break
            if lo <= hi and not any(s0 < hi and s1 > lo for s0, s1 in speech):
                chosen = int(round(min(max(ideal, lo), hi)))  # silent part: no word boundary needed
                break
        if chosen is None:
            lo, hi = prev + _f(smin * 0.8), fb - _f(smin * 0.8) * left
            chosen = int(round(min(max(ideal, lo), hi))) if lo <= hi else int(round(ideal))
        bounds.append(chosen)
    return bounds + [fb]


# ------------------------------------------------------------------ asset picking
class AssetPicker:
    """Each beat starts with its first (most relevant) asset, then rotates through the pool. A per-asset cursor
    advances through video so the same moment is not reused until fresh footage runs out. A still may follow
    itself (held, up to IMG_MAX); A-B-A-B alternation is refused."""

    def __init__(self, catalog: list[dict]):
        self.cat = {c["id"]: c for c in catalog}
        self.cursor: dict[str, float] = {}
        self.last_id, self.rot, self.held = None, 0, 0.0
        self.runs: list[str] = []  # asset id of each run of consecutive shots

    def note(self, a, dur: float) -> None:
        """Record that `a` is on screen for dur seconds."""
        if a["id"] == self.last_id:
            self.held += dur
        else:
            self.last_id, self.held = a["id"], dur
            self.runs.append(a["id"])

    def pool_for(self, beat: dict, bid: str, prev_pool) -> list[dict]:
        pool = []
        for a in (beat.get("visual") or {}).get("assets") or []:
            if a in self.cat:
                pool.append(self.cat[a])
            else:
                log.warning(f"{bid}: asset '{a}' not in catalog")
        pool += [c for c in self.cat.values() if c.get("beat") == bid and c not in pool]
        pool = pool or [c for c in self.cat.values() if not c.get("beat")] or list(prev_pool or [])
        if not pool:
            raise PipelineError(f"{bid}: no footage (catalog.json / visual.assets)")
        return pool

    def fallback_for(self, pool, prev_pool) -> list[dict]:
        out = []
        for c in [c for c in self.cat.values() if not c.get("beat")] + list(prev_pool or []):
            if c not in pool and c not in out:
                out.append(c)
        return out

    @staticmethod
    def is_video(a) -> bool:
        return a.get("kind", "video") == "video"

    @staticmethod
    def rng(a) -> tuple[float, float | None]:
        return float(a.get("from") or 0), (None if a.get("to") is None else float(a["to"]))

    def capacity(self, a) -> float:
        cap = float(a["maxDur"]) if a.get("maxDur") else math.inf
        lo, hi = self.rng(a)
        return max(0.0, min(cap, hi - lo) if self.is_video(a) and hi is not None else cap)

    def remaining(self, a) -> float:
        cap = float(a["maxDur"]) if a.get("maxDur") else math.inf
        lo, hi = self.rng(a)
        if self.is_video(a) and hi is not None:
            cap = min(cap, hi - self.cursor.get(a["id"], lo))
        return max(0.0, cap)

    def _ok(self, a, need, fresh) -> bool:
        if a["id"] == self.last_id:
            if not self.is_video(a) and self.held + need > IMG_MAX + 1e-6:
                return False  # still held long enough
        elif len(self.runs) >= 3 and a["id"] == self.runs[-2] and self.runs[-1] == self.runs[-3]:
            return False  # no A-B-A-B ping-pong
        return (self.remaining(a) if fresh else self.capacity(a)) >= need

    def pick(self, pool, need, fallback=None) -> dict:
        """Pool first, then fallback: fresh footage that fills the segment, then a useful fresh piece, then a still
        held longer, then (out of footage) rewound video."""
        k = self.rot % len(pool)
        order = pool[k:] + pool[:k]
        self.rot += 1
        small, cands = min(need, MIN_PIECE + 0.1), order + list(fallback or [])
        for n in (need, small):
            for a in cands:
                if self._ok(a, n, True):
                    return a
        stills = [a for a in cands if not self.is_video(a)]
        if stills:
            return next((a for a in stills if a["id"] == self.last_id), stills[0])
        return next((a for a in cands if self._ok(a, small, False)), None) or max(order, key=self.capacity)

    def consume(self, a, take: float) -> float | None:
        """Source in-point for video (cursor advances); None for images."""
        if not self.is_video(a):
            return None
        lo, hi = self.rng(a)
        cur = self.cursor.get(a["id"], lo)
        if hi is not None and cur + take > hi + 1e-6:
            cur = lo if lo + take <= hi + 1e-6 else max(0.0, hi - take)  # fresh footage used up: start over
            log.warning(f"'{a['id']}': fresh footage used up, replayed from {cur:.1f} s: add footage")
        self.cursor[a["id"]] = cur + take + 0.1
        return cur


def make_shot(a: dict, fa: int, fb: int, src_from, words, label_credit=True) -> dict:
    kind = a.get("kind", "video")
    s = {"src": (a.get("_src") or a["file"]).replace("\\", "/"), "kind": kind, "at": _s(fa), "dur": _s(fb - fa)}
    if kind == "video":
        s["from"] = _r(src_from or 0.0)
    s["focusX"] = round(float(a.get("focusX", 0.5)), 3)
    s["focusY"] = round(float(a.get("focusY", 0.5)), 3)
    zoom = a.get("motion") or (a.get("zoom") if isinstance(a.get("zoom"), str) else None)
    if zoom not in ("none", "punch", "slow"):
        if kind == "image":
            zoom = "slow"
        else:  # punch-in when a key word lands on the cut, unless fixed zoom + punch would pass the limit
            t0 = fa / FPS
            hit = any(w.get("key") and t0 - 0.05 <= w["start"] <= t0 + 0.2 for w in words)
            zoom = "punch" if hit and static_zoom(a) * PUNCH_PEAK <= 1.12 + 1e-6 else "none"
    s["zoom"] = zoom
    if a.get("illustrative"):
        s["illustrative"] = True
    if a.get("credit"):
        s["credit"] = str(a["credit"])
    return s


def fill_segment(picker: AssetPicker, pool, fa: int, fb: int, words, fallback=None) -> list[dict]:
    """Fill [fa, fb) with one shot, or several if the chosen source is short."""
    shots, f = [], fa
    while f < fb:
        rem = (fb - f) / FPS
        a = picker.pick(pool, rem, fallback)
        avail = picker.remaining(a)
        if avail < min(rem, MIN_PIECE):
            avail = picker.capacity(a)
        take, hold = rem, False
        if avail < rem:
            if rem - avail <= HOLD_MAX and picker.is_video(a) and avail >= MIN_PIECE:
                take, hold = avail, True
            else:
                take = min(avail, avail if rem - avail >= MIN_PIECE else max(MIN_PIECE, rem - MIN_PIECE))
            if take <= 0:
                raise PipelineError(f"asset '{a['id']}' has no usable duration (from/to?)")
        if hold:
            f2, fs = fb, f + max(1, int(take * FPS))
        else:
            f2 = fs = fb if take >= rem else min(fb, max(f + 1, f + _f(take)))
        shots.append(make_shot(a, f, fs, picker.consume(a, (fs - f) / FPS), words))
        picker.note(a, (f2 - f) / FPS)
        f = f2
    return shots


def plan_cued(picker: AssetPicker, pool, fallback, fa: int, fb: int, bw, cues: dict, lang: str,
              smin=SHOT_MIN, smax=SHOT_MAX, target=SHOT_TARGET) -> list[dict]:
    """visual.cues {asset id: word}: that asset starts as the word is spoken. If the asset is short the cut may
    slide into the word (up to its end); the rest is filled with un-cued assets."""
    byid = {c["id"]: c for c in pool + list(fallback or [])}
    marks = []  # [asset, earliest, ideal, latest] in frames
    for aid, cue in cues.items():
        a, i = byid.get(aid) or picker.cat.get(aid), find_cue(bw, cue, lang)
        if a is None or i is None:
            log.warning(f"cue '{aid}: {cue}' not found, skipped")
            continue
        w = bw[i]
        prev_end = bw[i - 1]["end"] if i else w["start"] - WORD_CUT_LEAD
        ideal = min(_f(max(prev_end, w["start"] - WORD_CUT_LEAD)), _f(w["start"]))
        marks.append([a, max(fa, ideal - _f(CUE_EARLY)), max(fa, ideal), max(fa, min(fb - 1, _f(w["end"])))])
    if not marks:
        return []
    marks.sort(key=lambda m: m[2])
    cued = {m[0]["id"] for m in marks}
    free = [c for c in pool if c["id"] not in cued]
    if marks[0][2] - fa < _f(CUE_PRE_MIN) or not free:
        marks[0][1] = marks[0][2] = fa
    starts = [m[2] for m in marks]
    for i in range(len(marks) - 1, -1, -1):  # backwards: each cued asset fits its section
        if i == 0 and not free and starts[0] == fa:
            continue
        end = starts[i + 1] if i + 1 < len(marks) else fb
        cap = min(picker.remaining(marks[i][0]), CUE_SHOT_MAX)
        if (end - starts[i]) / FPS > cap + 1e-6:
            starts[i] = min(marks[i][3], max(starts[i], end - int(cap * FPS)))
    for i in range(1, len(marks)):  # forwards: start a bit early if the previous asset runs short
        prev_cap = min(picker.remaining(marks[i - 1][0]), CUE_SHOT_MAX)
        if (starts[i] - starts[i - 1]) / FPS > prev_cap + 1e-6:
            starts[i] = max(marks[i][1], starts[i - 1] + int(prev_cap * FPS))
    if (fb - starts[-1]) / FPS < SHOT_FLOOR:  # cue near the beat end: start up to CUE_EARLY before the word
        lo = max(marks[-1][1], fb - _f(SHOT_FLOOR), starts[-2] + _f(SHOT_FLOOR) if len(starts) > 1 else fa)
        starts[-1] = min(starts[-1], lo)
    shots: list[dict] = []
    if starts[0] > fa:
        b = split_window(fa, starts[0], bw, smin, smax, target)
        for s0, s1 in zip(b, b[1:]):
            shots += fill_segment(picker, free or pool, s0, s1, bw, fallback)
    for i, m in enumerate(marks):
        f0, f1 = starts[i], (starts[i + 1] if i + 1 < len(marks) else fb)
        if f1 <= f0:
            continue
        a, L = m[0], (f1 - f0) / FPS
        avail = picker.remaining(a)
        take = min(L, avail, CUE_SHOT_MAX)
        if L - take < MIN_PIECE and avail >= L - 1e-6:
            take = L
        fm = fs = f1 if take >= L - 1e-6 else f0 + max(1, int(take * FPS))
        if fm < f1 and L - take <= HOLD_MAX and picker.is_video(a):
            fm = f1
        take = (fs - f0) / FPS
        if f1 == fb and take < SHOT_FLOOR <= picker.remaining(a):  # keep footage for tidy_shots to extend it
            take = SHOT_FLOOR
        shots.append(dict(make_shot(a, f0, fs, picker.consume(a, take), bw), _cue=True))
        picker.note(a, (fm - f0) / FPS)
        if fm < f1:
            shots += fill_segment(picker, free or pool, fm, f1, bw, fallback)
    return shots


# ------------------------------------------------------------------ shot clean-up
def _room(shots, k, a, skip, after=True) -> float:
    """Unused source seconds after (or before) shot k's footage; a still has unlimited room."""
    s = shots[k]
    if s["kind"] != "video":
        return math.inf
    lo, hi = AssetPicker.rng(a)
    hi = math.inf if hi is None else hi
    s0, s1 = s["from"], s["from"] + s["dur"]
    for j, o in enumerate(shots):
        if j == k or j in skip or o["src"] != s["src"]:
            continue
        o0, o1 = o["from"], o["from"] + o["dur"]
        if o0 >= s1 - 1e-6:
            hi = min(hi, o0)
        elif o1 <= s0 + 1e-6:
            lo = max(lo, o1)
        else:
            return 0.0  # already overlapping (replayed footage)
    return max(0.0, hi - s1 if after else s0 - lo)


def _grow(shots, k, end_f: int, a, skip) -> bool:
    """Shot k runs until frame end_f (its footage, then a hold of <= HOLD_MAX). False if the source lacks it."""
    s = shots[k]
    want = end_f - _f(s["at"])
    have = int((s["dur"] + _room(shots, k, a, skip)) * FPS + 1e-6) if s["kind"] == "video" else want
    if want - have > round(HOLD_MAX * FPS):
        return False
    s["dur"] = _s(min(want, have))
    return True


def tidy_shots(shots: list[dict], cat: dict, N: int) -> list[dict]:
    """Same still twice in a row -> one held shot; shots under SHOT_FLOOR merge into the previous shot (or pull
    the next one in) when that source has unused footage; more than two shots in a row from one source within a
    beat -> two longer shots. A cued shot keeps its start: if short, the next cut moves later."""
    shots = [dict(s) for s in shots]
    end = lambda j: _f(shots[j + 1]["at"]) if j + 1 < len(shots) else N  # noqa: E731

    def merge_stills():
        k = 1
        while k < len(shots):
            if shots[k]["kind"] == "image" and shots[k]["src"] == shots[k - 1]["src"]:
                _grow(shots, k - 1, end(k), None, ())
                del shots[k]
            else:
                k += 1

    merge_stills()
    k = 0
    while k < len(shots):
        if (end(k) - _f(shots[k]["at"])) / FPS >= SHOT_FLOOR - 1e-6 or len(shots) == 1:
            k += 1
            continue
        s, nxt, cued = shots[k], (shots[k + 1] if k + 1 < len(shots) else None), shots[k].get("_cue")
        if not cued and k and _grow(shots, k - 1, end(k), cat[shots[k - 1]["src"]], {k}):
            del shots[k]
            continue
        if not cued and nxt:  # the next shot starts earlier: earlier footage, else more of it
            extra, saved = _f(nxt["at"]) - _f(s["at"]), dict(nxt)
            back = _room(shots, k + 1, cat[nxt["src"]], {k}, after=False) >= extra / FPS - 1e-6
            nxt["at"] = s["at"]
            if back:
                nxt["dur"] = _s(_f(nxt["dur"]) + extra)
                if nxt["kind"] == "video":
                    nxt["from"] = _r(nxt["from"] - extra / FPS)
            if back or _grow(shots, k + 1, end(k + 1), cat[nxt["src"]], {k}):
                del shots[k]
                continue
            nxt.update(saved)
        c, floor = _f(s["at"]) + round(SHOT_FLOOR * FPS), round(SHOT_FLOOR * FPS)
        if nxt and end(k + 1) - c >= floor and _grow(shots, k, c, cat[s["src"]], ()):  # later cut, shorter next
            d = c - _f(nxt["at"])
            nxt["at"], nxt["dur"] = _s(c), _s(_f(nxt["dur"]) - d)
            if nxt["kind"] == "video":
                nxt["from"] = _r(nxt["from"] + d / FPS)
        elif not cued and k and end(k) - floor - _f(shots[k - 1]["at"]) >= floor:  # earlier cut, shorter previous
            prev, saved = shots[k - 1], dict(s)
            c = end(k) - floor
            s["at"] = _s(c)
            if _grow(shots, k, end(k), cat[s["src"]], ()):
                prev["dur"] = _s(min(_f(prev["dur"]), c - _f(prev["at"])))
            else:
                s.update(saved)
        k += 1
    merge_stills()
    i = 0
    while i < len(shots):
        j = i
        while j + 1 < len(shots) and shots[j + 1]["src"] == shots[i]["src"] \
                and shots[j + 1].get("_beat") == shots[i].get("_beat"):
            j += 1
        if j - i >= 2 and shots[i]["kind"] == "video" \
                and all(shots[x + 1]["from"] >= shots[x]["from"] for x in range(i, j)):
            a, mid = cat[shots[i]["src"]], (_f(shots[i]["at"]) + end(j)) / 2
            m = min(range(i + 1, j + 1), key=lambda x: abs(_f(shots[x]["at"]) - mid))
            if _grow(shots, m, end(j), a, set(range(i, j + 1)) - {m}):
                del shots[m + 1:j + 1]
                j = m
                if _grow(shots, i, _f(shots[m]["at"]), a, set(range(i + 1, m))):
                    del shots[i + 1:m]
                    j = i + 1
        i = j + 1
    return shots


def prep_zoom(job: Job, a: dict) -> str | None:
    """Numeric catalog zoom (<= 1.12) -> pre-cropped clip in assets/clips/_zoom/ (the contract has no numeric zoom).
    The window keeps the focus ratio, so the composition's cover crop shows the same framing as media.py sheet."""
    z = static_zoom(a)
    if z <= 1.001 or a.get("kind", "video") != "video":
        return None
    fx, fy = float(a.get("focusX", 0.5)), float(a.get("focusY", 0.5))
    src = job.a(a["file"])
    rel = f"clips/_zoom/{a['id']}__z{round(z * 100)}_x{round(fx * 100)}_y{round(fy * 100)}.mp4"
    dst = job.a(rel)
    if not (dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime):
        dst.parent.mkdir(parents=True, exist_ok=True)
        vf = f"crop=w=trunc(iw/{z:.4f}/2)*2:h=trunc(ih/{z:.4f}/2)*2:x=(iw-ow)*{fx:.4f}:y=(ih-oh)*{fy:.4f}"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-map", "0:v:0", "-vf", vf, "-c:v", "libx264",
             "-crf", "12", "-preset", "medium", "-pix_fmt", "yuv420p", "-an", dst])
    return rel


# ------------------------------------------------------------------ cards, tags, hook
def first_index(bw, phrase: str, lang: str) -> int | None:
    """Word index where the first two words of `phrase` are spoken."""
    q = [n for n in (norm(x, lang) for x in phrase.split()) if n][:2]
    ws = [norm(w["text"], lang) for w in bw]
    return next((i for i in range(len(ws) - len(q) + 1) if q and ws[i:i + len(q)] == q), None)


def word_cut(bw, i: int, floor: float) -> float:
    """Cut time just before word i."""
    return min(max(bw[i - 1]["end"] if i else floor, bw[i]["start"] - WORD_CUT_LEAD), bw[i]["start"])


def schedule_tags(overlays: list[dict], hook_until: float, stop: float) -> None:
    """Source tags share one lane: none during the hook, no overlap, at most TAG_SPILL past the beat end,
    at least TAG_MIN when delayed. Tags with no room (< 0.6 s) are dropped."""
    lane = hook_until + 0.05
    for o in sorted((o for o in overlays if o["type"] == "source"), key=lambda o: o["at"]):
        end = o.pop("_end")
        at = max(o["at"], lane)
        limit = min(stop, max(end + TAG_SPILL, at + TAG_MIN))
        if limit - at < 0.6:
            log.warning(f"source tag '{o['source']}' dropped: no room")
            overlays.remove(o)
            continue
        o["at"], o["dur"] = _r(at), _r(min(o["dur"], limit - at))
        lane = at + o["dur"] + 0.05


_SENT_END = re.compile(r"[.!?…]+[\"”'’)*]*$")


def hook_end(hook: dict, first_words, beat_end: float, lang: str) -> float:
    """hook.until: "sentence" (end of the first sentence), a word of the first beat, or seconds.
    Default: end of the first beat. Clamped to 1.8-4 s."""
    t, u = beat_end + 0.2, hook.get("until")
    if u == "sentence":
        t = next((w["end"] + 0.2 for w in first_words[:-1] if _SENT_END.search(w["text"])), t)
    elif isinstance(u, (int, float)) and not isinstance(u, bool):
        t = float(u)
    elif isinstance(u, str) and u:
        i = find_cue(first_words, u, lang)
        t = first_words[i]["end"] + 0.2 if i is not None else t
    return _r(min(HOOK_MAX, max(HOOK_MIN, t)))


def hook_repeats_speech(title: str, first_words, lang: str) -> bool:
    """Hook title mostly repeats the first beat -> captions off while the hook shows."""
    said = {norm(p, lang) for w in first_words for p in expand_spoken(w["text"], lang)}
    t = [norm(p, lang) for x in title.replace("*", " ").split() for p in expand_spoken(x, lang) if norm(p, lang)]
    return bool(t) and sum(x in said for x in t) / len(t) >= HOOK_DUP


_OPEN_Q, _CLOSE_Q = re.compile(r"^[“\"‘«]"), re.compile(r"[”\"’»][.,;:!?…]*$")


def join_short_quotes(words: list[dict]) -> list[dict]:
    """A two-word inline quote stays one caption chunk (opening and closing marks together)."""
    out, i = [], 0
    while i < len(words):
        w, n = words[i], words[i + 1] if i + 1 < len(words) else None
        if n and _OPEN_Q.match(w["text"]) and not _CLOSE_Q.search(w["text"]) and _CLOSE_Q.search(n["text"]) \
                and n.get("beat") == w.get("beat") and n["start"] - w["end"] < 0.35:
            out.append(dict(w, text=f"{w['text']} {n['text']}", end=max(w["end"], n["end"]),
                            key=bool(w.get("key") or n.get("key"))))
            i += 2
        else:
            out.append(dict(w))
            i += 1
    return out


def merge_ranges(rs: list[dict]) -> list[dict]:
    out = []
    for r in sorted(rs, key=lambda r: r["from"]):
        if out and r["from"] <= out[-1]["to"] + 0.05:
            out[-1]["to"] = max(out[-1]["to"], r["to"])
        else:
            out.append(dict(r))
    return [{"from": _r(r["from"]), "to": _r(r["to"])} for r in out]


def closing_start(words, closing: str | None, lang: str) -> float | None:
    """Start of the closing line if the narration ends with it."""
    want = [n for n in (norm(x, lang) for x in (closing or "").split()) if n]
    have = [norm(w["text"], lang) for w in words]
    return words[-len(want)]["start"] if want and have[-len(want):] == want else None


# ------------------------------------------------------------------ main
def build_props(job: Job, words_data: dict | None = None, catalog: list[dict] | None = None) -> dict:
    data, ch, lang = job.data, job.channel, job.lang
    wd = words_data or load_json(job.b("words.json"))
    words, bt = wd["words"], {b["id"]: b for b in wd["beats"]}
    beats = data["beats"]
    ids = [beat_id(b, i) for i, b in enumerate(beats)]
    for i in ids:
        if i not in bt:
            raise PipelineError(f"beat {i} missing in words.json: rerun align")
    edit = data.get("edit") or {}
    smin, smax = float(edit.get("shotMin", SHOT_MIN)), float(edit.get("shotMax", SHOT_MAX))
    target = float(edit.get("shotTarget", SHOT_TARGET))

    cs = closing_start(words, ch.get("closingLine"), lang)
    outro_at = _r(max(cs - 0.05 if cs is not None else words[-1]["end"] + OUTRO_GAP, bt[ids[-1]]["start"]))
    dur = math.ceil(max(outro_at + OUTRO_SEC, float(wd["duration"]) + 0.1, words[-1]["end"] + 0.3) * 100 - 1e-6) / 100
    N = ceil_frames(dur)
    D = N / FPS
    starts, ends = [bt[i]["start"] for i in ids], [bt[i]["end"] for i in ids]
    cut = [0]
    for k in range(1, len(ids)):
        cut.append(max(_f(min(max(ends[k - 1], starts[k] - CUT_LEAD), starts[k])), cut[-1] + 9))
    cut.append(N)
    if cut[-2] >= N - 3:
        raise PipelineError("no time left for the last beat (words.json inconsistent?)")

    live = catalog is None
    cat = [dict(c) for c in (job.catalog() if live else catalog)]
    if live:
        used = {x for b in beats for x in ((b.get("visual") or {}).get("assets") or [])}
        errs, warns = check_framing([c for c in cat if c["id"] in used and job.a(c["file"]).exists()],
                                    lambda c: media_size(job.a(c["file"])))
        for w in warns:
            log.warning(f"framing: {w}")
        if errs:
            raise PipelineError("framing rules violated:\n  " + "\n  ".join(errs))
        for c in cat:
            if not job.a(c["file"]).exists():
                raise PipelineError(f"{c['id']}: file missing assets/{c['file']}")
            if c.get("kind", "video") == "video" and c.get("to") is None:
                c["to"] = round(ffprobe_duration(job.a(c["file"])) - 0.05, 3)
            c["_src"] = prep_zoom(job, c)
    picker = AssetPicker(cat)
    shots, overlays, sfx, cap_off = [], [], [], []
    first_words = [w for w in words if w["beat"] == ids[0]]
    hook = data["hook"]
    hook_until = hook_end(hook, first_words, ends[0], lang)
    prev_pool = None

    def add_sfx(name, at, vol):
        if at is not None and 0 <= at < D:
            sfx.append({"src": f"sfx/{name}.wav", "at": _r(at), "volume": vol})

    add_sfx("whoosh", 0.0, 0.35)
    for k, (beat, bid) in enumerate(zip(beats, ids)):
        fa, fb = cut[k], cut[k + 1]
        a, b = fa / FPS, fb / FPS
        bw = [w for w in words if w["beat"] == bid]
        pool = picker.pool_for(beat, bid, prev_pool)
        fallback = picker.fallback_for(pool, prev_pool)
        picker.rot = 0
        cues = (beat.get("visual") or {}).get("cues") or {}
        D_b, n0 = (fb - fa) / FPS, len(shots)
        have = sum(picker.remaining(c) if picker.is_video(c) else IMG_MAX for c in pool)
        if have < D_b - 0.1:
            log.warning(f"{bid}: only {len(pool)} asset{'s' * (len(pool) != 1)} for {D_b:.1f} s - add footage")
        cued = plan_cued(picker, pool, fallback, fa, fb, bw, cues, lang, smin, smax, target) if cues else []
        if cued:
            shots += cued
        else:  # short-clip pool: each asset once, proportional to its length (no repeats or slivers)
            fresh = [c for c in pool if picker.is_video(c) and picker.remaining(c) >= smin * 0.75
                     and not c.get("maxDur")]
            caps = None
            if len(fresh) == len(pool) and round(D_b / target) >= len(fresh) >= 2 \
                    and sum(min(picker.remaining(c), smax) for c in fresh) < D_b + len(fresh) * smin:
                caps = [min(picker.remaining(c), smax) + HOLD_MAX for c in fresh]
            bounds = split_window(fa, fb, bw, smin, smax, target, caps)
            for s0, s1 in zip(bounds, bounds[1:]):
                shots += fill_segment(picker, pool, s0, s1, bw, fallback)
        for s in shots[n0:]:
            s["_beat"] = bid
        prev_pool = pool

        ov, src = beat.get("overlay"), beat.get("source")
        if ov:
            c0 = a + (0.1 if k else 0.0)
            if ov["type"] == "quote":  # the card arrives when the quote is spoken
                qi = first_index(bw, ov.get("quote", ""), lang)
                if qi is not None and bw[qi]["start"] - QUOTE_LEAD - a >= QUOTE_PRE_MIN:
                    c0 = _s(_f(bw[qi]["start"] - QUOTE_LEAD))
            c0 = max(c0, hook_until + 0.05) if k == 0 else c0
            c1 = min(b, c0 + CARD_MAX)
            ui = find_cue(bw, ov["until"], lang) if ov.get("until") else None
            if ui is not None:
                c1 = min(c1, word_cut(bw, ui, a))
            c1 = min(b, max(c1, c0 + CARD_MIN))
            if c1 - c0 >= 0.6:
                if ov["type"] == "stat":
                    o = {"type": "stat", "at": _r(c0), "dur": _r(c1 - c0), "title": ov["title"],
                         **({"unit": ov["unit"]} if ov.get("unit") else {}),
                         "items": [{"label": str(x["label"]), "value": x["value"],
                                    **({"highlight": True} if x.get("highlight") else {})} for x in ov["items"]]}
                else:
                    o = {"type": "quote", "at": _r(c0), "dur": _r(c1 - c0), "quote": ov["quote"], "name": ov["name"]}
                    o.update({k2: ov[k2] for k2 in ("role", "highlight") if ov.get(k2)})
                overlays.append(o)
                cap_off.append({"from": c0, "to": c1})
                add_sfx("pop", c0 + 0.03, 0.4)
        if src and src.get("name"):
            t0 = max(a + 0.1, bt[bid]["start"]) if k else 0.15
            o = {"type": "source", "at": t0, "dur": min(SOURCE_MAX, max(SOURCE_MIN, b - t0 - 0.1)),
                 "source": src["name"], "_end": b}
            if src.get("date"):
                o["date"] = src["date"]
            overlays.append(o)
    schedule_tags(overlays, hook_until, min(D, outro_at))

    sfx_info = load_json(job.b("sfx_info.json")) if live and job.b("sfx_info.json").exists() else {}
    if sfx_info.get("files"):
        add_sfx("whoosh", outro_at - 0.1, 0.3)
    else:
        sfx = []
    theme = ch.get("theme") or {}
    props = {"durationSec": dur,
             "theme": {"accent": theme.get("accent", "#FFD400"), "text": theme.get("text", "#FFFFFF"),
                       "dark": theme.get("dark", "#111111"), "font": theme.get("font", "Inter"),
                       "channelName": ch.get("name", ""), "handle": ch.get("handle", ""),
                       "illustrativeLabel": (ch.get("labels") or {}).get("illustrative", "ILLUSTRATIVE")},
             "voice": {"src": "voice.wav", "volume": 1}}
    if sfx_info.get("music"):
        props["music"] = {"src": sfx_info["music"]["src"], "volume": float(sfx_info["music"].get("volume", MUSIC_VOL))}
    props["sfx"] = sorted(sfx, key=lambda s: s["at"])
    props["words"] = [{"text": w["text"], "start": w["start"], "end": w["end"], **({"key": True} if w.get("key") else {})}
                      for w in join_short_quotes(words)]
    if hook_repeats_speech(hook["title"], first_words, lang):
        cap_off.append({"from": 0.0, "to": hook_until})
    if cs is not None:  # the outro shows the closing line: no duplicate caption
        cap_off.append({"from": outro_at, "to": D})
    props["captionsOff"] = merge_ranges([r for r in cap_off if r["to"] > r["from"]])
    props["hook"] = {"title": hook["title"], "until": hook_until}
    bysrc = {(c.get("_src") or c["file"]).replace("\\", "/"): c for c in cat}
    props["shots"] = [{k2: v for k2, v in s.items() if k2[0] != "_"} for s in tidy_shots(shots, bysrc, N)]
    props["overlays"] = sorted(overlays, key=lambda o: o["at"])
    props["outro"] = {"at": outro_at, "text": ch.get("closingLine") or ""}
    return props


# ------------------------------------------------------------------ validation
def validate_props(p: dict, assets_dir=None) -> list[str]:
    E, tol = [], 1 / FPS + 1e-6
    if set(p) - TOP_KEYS:
        E.append(f"unknown keys {sorted(set(p) - TOP_KEYS)}")
    if REQUIRED_KEYS - set(p):
        return E + [f"missing key {k}" for k in sorted(REQUIRED_KEYS - set(p))]
    D = p["durationSec"]
    N = ceil_frames(D)
    if set(p["theme"]) != THEME_KEYS:
        E.append(f"theme keys {sorted(p['theme'])}")
    files = [p["voice"]["src"]] + ([p["music"]["src"]] if p.get("music") else [])
    for s in p.get("sfx", []):
        if set(s) - {"src", "at", "volume"} or not 0 <= s["at"] < D:
            E.append(f"bad sfx {s}")
        files.append(s["src"])
    for w in p["words"]:
        if set(w) - {"text", "start", "end", "key"} or not 0 <= w["start"] <= w["end"] <= D + tol:
            E.append(f"bad word {w}")
    for r in p.get("captionsOff", []):
        if set(r) != {"from", "to"} or not 0 <= r["from"] < r["to"] <= D + tol:
            E.append(f"bad captionsOff {r}")
    if set(p["hook"]) != {"title", "until"} or not 0 < p["hook"]["until"] <= D:
        E.append(f"bad hook {p['hook']}")
    fpos = 0
    for i, s in enumerate(p["shots"]):
        if set(s) - SHOT_KEYS or s.get("kind") not in ("video", "image") or \
                s.get("zoom", "none") not in ("none", "punch", "slow") or s["dur"] <= 0:
            E.append(f"shot {i}: bad fields {s}")
        fa = round(s["at"] * FPS)
        if not fpos <= fa <= fpos + round(HOLD_MAX * FPS):
            E.append(f"shot {i}: gap/overlap (frame {fa}, expected {fpos})")
        fpos = round((s["at"] + s["dur"]) * FPS)
        files.append(s["src"])
    if fpos < N - round(HOLD_MAX * FPS):
        E.append(f"shots end at frame {fpos} of {N}")
    for o in p["overlays"]:
        keys = OVERLAY_KEYS.get(o.get("type"))
        if keys is None or set(o) - keys or not 0 <= o["at"] < D or o["dur"] <= 0 or o["at"] + o["dur"] > D + tol:
            E.append(f"bad overlay {o}")
    if set(p["outro"]) != {"at", "text"} or not 0 <= p["outro"]["at"] <= D - 2.0 + tol:
        E.append(f"bad outro {p['outro']} (needs >= 2 s before the end)")
    if assets_dir is not None:
        E += [f"missing file assets/{f}" for f in sorted(set(files)) if not (assets_dir / f).exists()]
    return E


def run_timeline(job: Job) -> dict:
    props = build_props(job)
    errs = validate_props(props, job.assets)
    if errs:
        raise PipelineError("props.json invalid:\n  " + "\n  ".join(errs[:20]))
    save_json(job.b("props.json"), props)
    lens = [s["dur"] for s in props["shots"]]
    log.info(f"timeline: {props['durationSec']} s, {len(lens)} shots ({min(lens):.2f}-{max(lens):.2f} s), "
             f"{len(props['overlays'])} overlays -> build/props.json")
    return props
