"""faster-whisper word timings mapped onto the script with difflib -> build/words.json
{sig, duration, match, words: [{text, start, end, key, beat}], beats: [{id, start, end}]}.
Also holds leak detection used by the tts stage (spoken text before/after the script)."""
from __future__ import annotations

import difflib
import wave

from common import Job, PipelineError, beat_id, db_frames, decode_audio, expand_spoken, ffprobe_duration, file_sig, \
    load_json, log, narration_text, norm, save_json, script_tokens, sha1

_MODELS: dict = {}


def transcribe(path, size: str = "medium", lang: str = "en") -> list[tuple[float, float, str]]:
    """[(start, end, word)] with word timestamps."""
    if size not in _MODELS:
        from faster_whisper import WhisperModel
        _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8")
    segs, _ = _MODELS[size].transcribe(decode_audio(path), language=lang, word_timestamps=True, beam_size=5,
                                       condition_on_previous_text=False)
    return [(float(w.start), float(w.end), w.word.strip()) for s in segs for w in (s.words or []) if w.word.strip()]


# ------------------------------------------------------------------ units
def script_units(tokens: list[dict], lang: str = "en") -> list[tuple[str, int]]:
    """Script words expanded to spoken units: [(norm, token_index)]."""
    return [(n, ti) for ti, t in enumerate(tokens) for w in (t.get("tts") or t["display"]).split()
            for p in expand_spoken(w, lang) for n in [norm(p, lang)] if n]


def whisper_units(hw, lang: str = "en") -> list[tuple[float, float, str, str]]:
    """Whisper words expanded the same way; duration split by letter count. [(start, end, norm, original)]"""
    out = []
    for s, e, w in hw:
        parts = [n for n in (norm(p, lang) for p in expand_spoken(w, lang)) if n]
        total, acc = sum(len(p) for p in parts), s
        for p in parts:
            d = (e - s) * len(p) / total
            out.append((acc, acc + d, p, w))
            acc += d
    return out


def _matcher(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False)


def _monotonic(words: list[dict]) -> None:
    for k in range(1, len(words)):
        if words[k]["start"] < words[k - 1]["start"] + 0.01:
            words[k]["start"] = round(words[k - 1]["start"] + 0.01, 3)
        words[k]["end"] = max(words[k]["end"], round(words[k]["start"] + 0.06, 3))
        if words[k - 1]["end"] > words[k]["start"] and words[k]["start"] - words[k - 1]["start"] >= 0.06:
            words[k - 1]["end"] = words[k]["start"]


def align_tokens(tokens: list[dict], hw, lang: str = "en") -> tuple[list[dict], float]:
    """Assign start/end to script words. Returns (words, exact match ratio)."""
    units, hu = script_units(tokens, lang), whisper_units(hw, lang)
    if not units:
        raise PipelineError("script has no words")
    if not hu:
        raise PipelineError("whisper found no words (is the audio empty?)")
    a, b = [u[0] for u in units], [h[2] for h in hu]
    times: list = [None] * len(units)
    exact = 0
    for tag, i1, i2, j1, j2 in _matcher(a, b).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                times[i1 + k] = (hu[j1 + k][0], hu[j1 + k][1])
            exact += (i2 - i1) if tag == "equal" else 0
        elif tag == "replace":  # different lengths: split the span by letter count
            t0, t1 = hu[j1][0], hu[j2 - 1][1]
            L = [max(1, len(a[i])) for i in range(i1, i2)]
            acc = t0
            for i, n in zip(range(i1, i2), L):
                d = (t1 - t0) * n / sum(L)
                times[i], acc = (acc, acc + d), acc + d
    i = 0
    while i < len(times):  # unmatched units: spread evenly between neighbours
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        prev = times[i - 1][1] if i else max(0.0, (times[j][0] if j < len(times) else 0.0) - 0.3 * (j - i))
        nxt = max(times[j][0] if j < len(times) else prev + 0.3 * (j - i), prev + 0.12 * (j - i))
        step = (nxt - prev) / (j - i)
        for k in range(i, j):
            times[k] = (prev + step * (k - i), prev + step * (k - i + 1))
        i = j
    words = []
    for ti, t in enumerate(tokens):
        ts = [times[k] for k, (_, owner) in enumerate(units) if owner == ti]
        if not ts:  # punctuation only: glue to previous word
            if words:
                words[-1]["text"] += t["display"]
            continue
        words.append({"text": t["display"], "start": round(min(x[0] for x in ts), 3),
                      "end": round(max(x[1] for x in ts), 3), "key": bool(t.get("key")), "beat": t.get("beat")})
    _monotonic(words)
    return words, exact / len(units)


# ------------------------------------------------------------------ boundary fixes
def silence_runs(path, min_len: float = 0.12, rel_db: float = 32, abs_db: float = -42) -> list[tuple[float, float]]:
    """Silent ranges (10 ms windows, rel_db below speech p90)."""
    import numpy as np
    db = db_frames(decode_audio(path), 160)
    if len(db) < 5:
        return []
    sil = db < min(abs_db, float(np.percentile(db, 90)) - rel_db)
    runs, i, n = [], 0, len(db)
    while i < n:
        if sil[i]:
            j = i
            while j < n and sil[j]:
                j += 1
            if (j - i) / 100 >= min_len:
                runs.append((i / 100, j / 100))
            i = j
        else:
            i += 1
    return runs


ONSET_MAX = 0.45


def refine_with_silence(words: list[dict], runs, fine_runs=None) -> int:
    """Whisper folds pauses into words. Trim silence at a word's head/tail; with fine_runs (>=60 ms) also pull a
    late word start back to the end of the preceding pause (at most ONSET_MAX). Returns number of fixes."""
    fixed = 0
    for w in words:
        s, e = w["start"], w["end"]
        for a, b in runs:
            if b <= s or a >= e:
                continue
            if a <= s + 0.05 and b < e - 0.05:
                s = b
            elif a > s + 0.06 and b >= e - 0.02:
                e = a
        if (s, e) != (w["start"], w["end"]) and e - s >= 0.06:
            w["start"], w["end"] = round(s, 3), round(e, 3)
            fixed += 1
    for k, w in enumerate(words if fine_runs else []):
        s, prev_end = w["start"], words[k - 1]["end"] if k else 0.0
        ends = [b for a, b in fine_runs if prev_end - 0.02 <= b <= s + 0.02 and a < s]
        if ends and max(ends) < s - 0.06 and s - max(ends) <= ONSET_MAX and w["end"] - max(ends) >= 0.06:
            w["start"] = round(max(ends), 3)
            fixed += 1
    _monotonic(words)
    return fixed


def skip_breath(words: list[dict], path, rel_db=22, lo=0.2, hi=0.7, gap=0.2) -> int:
    """After a pause whisper may start a word on the breath. If the first lo..hi s of the word stay rel_db below
    speech level, move the start to the first speech frame."""
    import numpy as np
    db = db_frames(decode_audio(path), 160)
    if len(db) < 5 or not words:
        return 0
    thr, fixed = float(np.percentile(db, 90)) - rel_db, 0
    for k in range(1, len(words)):
        w = words[k]
        if w["start"] - words[k - 1]["end"] < gap:
            continue
        i0 = int(round(w["start"] * 100))
        i1 = min(len(db), int(round(min(w["end"], w["start"] + hi + 0.05) * 100)))
        j = i0
        while j < i1 and db[j] < thr:
            j += 1
        if j < i1 and lo <= (j - i0) / 100 <= hi and w["end"] - j / 100 >= 0.06:
            w["start"] = round(j / 100 - 0.02, 3)
            fixed += 1
    return fixed


def trim_tail(path, last_end: float, fine_runs, keep: float = 0.15, fade: float = 0.02) -> dict | None:
    """Cut audio after the last word (TTS sometimes ends with a noise burst). Edits the 16-bit wav in place."""
    import numpy as np
    after = [a for a, b in fine_runs if b > last_end + 0.02]
    if not after:
        return None
    cut = max(last_end, min(after)) + keep
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        return None
    x = np.frombuffer(raw, np.int16).reshape(-1, ch)
    i0, hop = int(round(cut * sr)), max(1, int(sr * 0.01))
    if i0 >= len(x) - hop:
        return None
    mono = x.astype(np.float32).mean(1) / 32768
    db = db_frames(mono, hop)
    if not (db[i0 // hop + 1:] >= min(-42.0, float(np.percentile(db, 90)) - 32)).any():
        return None
    y = x[:i0].astype(np.float32)
    nf = min(len(y), max(1, int(fade * sr)))
    y[-nf:] *= np.linspace(1, 0, nf)[:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.clip(y, -32768, 32767).astype(np.int16).tobytes())
    return {"cut": round(cut, 3), "removed_sec": round((len(x) - i0) / sr, 3)}


def beat_times(words: list[dict], beats: list[dict]) -> list[dict]:
    out = []
    for i, beat in enumerate(beats):
        bid = beat_id(beat, i)
        ws = [w for w in words if w["beat"] == bid]
        if not ws:
            raise PipelineError(f"beat {bid} has no words")
        out.append({"id": bid, "start": ws[0]["start"], "end": ws[-1]["end"]})
    return out


# ------------------------------------------------------------------ leak detection (tts stage)
def detect_leak(tokens, hw, lang: str = "en", min_extra: int = 2, min_extra_sec: float = 0.6) -> dict:
    """Extra speech before (or after) the script in a raw TTS take, e.g. the style note read aloud."""
    units, hu = script_units(tokens, lang), whisper_units(hw, lang)
    res = {"cut_start": None, "cut_end": None, "lead_words": [], "match": 0.0, "tail_ok": False}
    if not units or not hu:
        return res
    a, b = [u[0] for u in units], [h[2] for h in hu]
    blocks = [m for m in _matcher(a, b).get_matching_blocks() if m.size > 0]
    if not blocks:
        return res
    res["match"] = sum(m.size for m in blocks) / len(a)
    res["tail_ok"] = blocks[-1].a + blocks[-1].size >= len(a) - 1
    # head: first solid anchor block (>=3, else >=2 units); single-word matches are not trusted
    first = next((m for m in blocks if m.size >= 3), None) or next((m for m in blocks if m.size >= 2), blocks[0])
    extra = first.b - first.a
    if 0 < extra < len(hu):
        cands = [j for j in (extra - 1, extra, extra + 1) if 1 <= j < len(hu) and j <= first.b]
        j = max(cands, key=lambda j: (round(hu[j][0] - hu[j - 1][1], 2), j == extra))
        if j >= min_extra or hu[j][0] - hu[0][0] >= min_extra_sec:
            res["cut_start"] = round(max(hu[j - 1][1], hu[j][0] - 0.06), 3)
            res["lead_words"] = list(dict.fromkeys(u[3] for u in hu[:j]))
    last = next((m for m in reversed(blocks) if m.size >= 2), blocks[-1])
    rem_script = len(a) - (last.a + last.size)
    if len(b) - (last.b + last.size) - rem_script >= 3:
        res["cut_end"] = round(hu[last.b + last.size - 1 + rem_script][1] + 0.15, 3)
    return res


# ------------------------------------------------------------------ stage
def run_align(job: Job, force: bool = False) -> dict:
    voice, out = job.a("voice.wav"), job.b("words.json")
    if not voice.exists():
        raise PipelineError("assets/voice.wav missing: run tts or pass --voice")
    size = (job.channel.get("whisper") or {}).get("model", "medium")
    toks = script_tokens(job.data)

    def sig():
        return sha1(narration_text(job.data), [(t["display"], t["key"], t["beat"]) for t in toks], file_sig(voice),
                    size, job.lang)
    if out.exists() and not force and load_json(out).get("sig") == sig():
        log.info("align: up to date")
        return load_json(out)
    hw = transcribe(voice, size, job.lang)
    words, ratio = align_tokens(toks, hw, job.lang)
    fine = silence_runs(voice, min_len=0.06)
    n_fix = refine_with_silence(words, silence_runs(voice), fine) + skip_breath(words, voice)
    tail = trim_tail(voice, words[-1]["end"], fine)
    data = {"sig": sig(), "duration": round(ffprobe_duration(voice), 3), "match": round(ratio, 3), "model": size,
            "words": words, "beats": beat_times(words, job.beats), "whisper": " ".join(h[2] for h in hw)}
    if tail:
        data["tail_trim"] = tail
    save_json(out, data)
    (log.warning if ratio < 0.7 else log.info)(
        f"align: {len(words)} words, match {ratio:.0%}, {data['duration']:.2f} s, {n_fix} boundary fixes"
        + (f", tail cut at {tail['cut']:.2f} s" if tail else ""))
    return data
