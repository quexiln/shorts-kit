"""Shared helpers: paths, Job, channel config, JSON io, signatures, ffmpeg, text parsing."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

PIPE = Path(__file__).resolve().parent
ROOT = PIPE.parent
REMOTION = ROOT / "remotion"
FPS = 30
W, H = 1080, 1920
SAFE = dict(top=220, bottom=H - 420, left=60, right=W - 150)  # pixel bounds of the safe area
STAGES = ("tts", "align", "sfx", "timeline", "render", "qa", "meta", "cleanup")
LIVE_STAGES = ("tts", "align", "sfx", "timeline", "render", "qa")
# small reproduction files copied to keep/ by cleanup: (live folder, file)
KEEP_FILES = (("assets", "catalog.json"), ("assets", "voice.wav"), ("build", "words.json"),
              ("build", "props.json"), ("build", "tts_info.json"), ("build", "qa_report.json"))
PLATFORMS = ("youtube", "instagram", "tiktok")

log = logging.getLogger("shorts")


class PipelineError(RuntimeError):
    pass


# ------------------------------------------------------------------ channel config
DEFAULT_CHANNEL = {
    "name": "My Channel", "handle": "@mychannel", "language": "en", "closingLine": "What do you think?",
    "tts": {"model": "gemini-3.8-flash-tts", "voice": "Orus", "style": "Calm, confident, conversational narrator",
            "tempo": 1.0, "fallbackModels": [], "allowFallback": False},
    "whisper": {"model": "medium"},
    "theme": {"accent": "#FFD400", "text": "#FFFFFF", "dark": "#111111", "font": "Inter"},
    "labels": {"illustrative": "ILLUSTRATIVE"},
    "music": None,
    "platforms": {"youtube": {"titleMax": 100}, "instagram": {"hashtagsMax": 5}, "tiktok": {"captionMax": 2200}},
    "bannedPublicPhrases": [],
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_channel(root: Path = ROOT) -> dict:
    p = root / "channel.json"
    if not p.exists():
        p = root / "channel.example.json"
        if p.exists():
            log.warning("channel.json missing: using channel.example.json (copy it to channel.json)")
    return _merge(DEFAULT_CHANNEL, load_json(p) if p.exists() else {})


# ------------------------------------------------------------------ job folder
class Job:
    def __init__(self, job_dir, channel: dict | None = None):
        self.dir = Path(job_dir).resolve()
        self.json_path = self.dir / "job.json"
        if not self.json_path.exists():
            raise PipelineError(f"no job.json in {self.dir}")
        self.data = load_json(self.json_path)
        self.channel = channel if channel is not None else load_channel()
        self.lang = str(self.channel.get("language") or "en").lower()
        self.assets, self.build, self.keep = self.dir / "assets", self.dir / "build", self.dir / "keep"

    def a(self, *p) -> Path:
        return self.assets.joinpath(*p)

    def b(self, *p) -> Path:
        return self.build.joinpath(*p)

    @property
    def beats(self) -> list[dict]:
        return self.data.get("beats", [])

    @property
    def is_cleaned(self) -> bool:
        return not self.assets.exists() and any(
            p.exists() for p in (self.dir / "final.mp4", self.dir / "meta.json", self.keep))

    def find(self, name: str) -> Path | None:
        """Small production file: live (assets/ or build/) first, then keep/."""
        live = dict((f, d) for d, f in KEEP_FILES).get(name, "build")
        for p in ((self.assets if live == "assets" else self.build) / name, self.keep / name):
            if p.exists():
                return p
        return None

    def tts_opt(self, key: str, default=None):
        jt = self.data.get("tts") or {}
        return jt[key] if key in jt else (self.channel.get("tts") or {}).get(key, default)

    def catalog(self) -> list[dict]:
        p = self.a("catalog.json")
        d = load_json(p) if p.exists() else []
        return (d.get("items") or []) if isinstance(d, dict) else d


# ------------------------------------------------------------------ io
def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, data, indent=1):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.write("\n")
    os.replace(tmp, p)


def sha1(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, ensure_ascii=False, sort_keys=True).encode())
    return h.hexdigest()[:16]


def file_sig(p) -> list:
    p = Path(p)
    if not p.exists():
        return [p.name, None]
    st = p.stat()
    return [p.name, st.st_size, int(st.st_mtime)]


def qa_ok(rep: dict | None) -> bool:
    return bool(rep) and (bool(rep.get("ok")) or bool(rep.get("reviewed")))


def setup_logging(level=logging.INFO):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    log.setLevel(level)
    log.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(levelname).1s %(message)s"))
    log.addHandler(h)
    log.propagate = False
    return log


# ------------------------------------------------------------------ commands / media
def run(cmd, cwd=None, check=True, timeout=None) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    if check and r.returncode:
        raise PipelineError(f"command failed ({r.returncode}): {' '.join(cmd[:5])} ...\n{(r.stderr or '')[-1500:]}")
    return r


def ffprobe_duration(p) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout)


def ffprobe_streams(p) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=codec_type,width,height,r_frame_rate:format=duration,size", "-of", "json", p]).stdout)


def media_size(p) -> tuple[int, int] | None:
    """Pixel size of a video or image; None if unreadable."""
    try:
        v = next((s for s in ffprobe_streams(p).get("streams", []) if s.get("codec_type") == "video"), None)
        return (int(v["width"]), int(v["height"])) if v else None
    except (PipelineError, ValueError, KeyError):
        return None


def decode_audio(p, sr=16000):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(p), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def db_frames(x, hop: int):
    """RMS level in dBFS per window of `hop` samples."""
    import numpy as np
    n = len(x) // hop
    return 20 * np.log10(np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-9)


def ceil_frames(sec: float) -> int:
    """Composition length in frames; same as Math.ceil(durationSec * 30) in JS."""
    return int(math.ceil(sec * FPS))


# ------------------------------------------------------------------ locale text
DOTTED = ("tr", "az")


def lower(s: str, lang: str = "en") -> str:
    return (s.replace("I", "ı").replace("İ", "i") if lang in DOTTED else s).lower()


def upper(s: str, lang: str = "en") -> str:
    return (s.replace("i", "İ").replace("ı", "I") if lang in DOTTED else s).upper()


def norm(s: str, lang: str = "en") -> str:
    """Matching key: locale lowercase, no punctuation, accents folded to ASCII."""
    s = unicodedata.normalize("NFKD", lower(s, lang).replace("ı", "i"))
    return re.sub(r"[\W_]", "", "".join(c for c in s if not unicodedata.combining(c)))


DECIMAL_COMMA = {"tr", "az", "de", "fr", "es", "it", "pt", "nl", "ru", "pl", "sv", "da", "nb", "fi", "cs", "ro", "id"}
PERCENT = {"en": "percent", "tr": "yüzde", "az": "faiz", "de": "prozent", "fr": "pour cent", "es": "por ciento",
           "it": "per cento", "pt": "por cento", "nl": "procent"}
_TR1 = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TR10 = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]


def _tr_num(n: int) -> list[str]:
    if n == 0:
        return ["sıfır"]
    out = []
    for v, name in ((10 ** 9, "milyar"), (10 ** 6, "milyon"), (1000, "bin")):
        q, n = divmod(n, v)
        if q:
            out += ([] if (v == 1000 and q == 1) else _tr_num(q)) + [name]
    h, n = divmod(n, 100)
    if h:
        out += ([_TR1[h]] if h > 1 else []) + ["yüz"]
    t, o = divmod(n, 10)
    return out + [x for x in (_TR10[t], _TR1[o]) if x]


def spell_number(num: str, lang: str = "en") -> list[str]:
    """'1500' -> words: built-in Turkish speller (spaced words), else num2words (optional), else the digits."""
    if lang == "tr" and "." not in num:
        return _tr_num(int(num))
    try:
        from num2words import num2words
        return num2words(float(num) if "." in num else int(num), lang=lang).replace("-", " ").replace(",", "").split()
    except (ImportError, NotImplementedError, OverflowError, ValueError, KeyError, TypeError):
        return [num]


def expand_spoken(word: str, lang: str = "en") -> list[str]:
    """Written form -> spoken units: '40%' -> ['forty', 'percent'], '%40' (tr) -> ['yüzde', 'kırk']."""
    w = word.strip()
    if not re.search(r"\d", w):
        return [w]
    comma = lang in DECIMAL_COMMA
    w = re.sub(r"(?<=\d)" + (r"\." if comma else ",") + r"(?=\d{3}(?!\d))", "", w)  # thousands separator
    parts: list[str] = []
    for m in re.finditer(r"%|\d+(?:[.,]\d+)?|[^\d%]+", w):
        p = m.group()
        if p == "%":
            parts += PERCENT.get(lang, "percent").split()
        elif p[0].isdigit():
            parts += [p] if len(p) > 13 else spell_number(p.replace(",", "."), lang)
        else:
            suffix = re.sub(r"[\W_]", "", p)
            if suffix and parts and not re.fullmatch(r"[\s\-–/:]+", p) and p[0] in "'’":
                parts[-1] += suffix  # attached suffix: 90'dan -> doksandan
            elif suffix:
                parts.append(suffix)
    return parts or [w]


# ------------------------------------------------------------------ script text
_TAIL_PUNCT = re.compile(r"[.,!?;:…]+$")
_LEAD_QUOTE = re.compile(r"^[\"'“‘(«]+")
_TAIL_QUOTE = re.compile(r"[\"'”’)»]+$")
TTS_TAG = re.compile(r"<\s*([A-Za-z][A-Za-z \-]{0,30}?)\s*>")
TTS_TAGS_KNOWN = {"short pause", "long pause", "breath", "sigh", "laugh", "gasp", "groan", "cough",
                  "throat-clearing", "sneeze", "yawn"}


def _core(tok: str) -> str:
    t, prev = _LEAD_QUOTE.sub("", tok), None
    while prev != t:
        prev, t = t, _TAIL_PUNCT.sub("", _TAIL_QUOTE.sub("", t))
    return t


def parse_text(text: str) -> list[dict]:
    """Beat text -> [{'display', 'tts', 'key'}].
    '*word*' / '*two words*' mark key words; 'written|spoken' splits caption and speech; '_' joins a multi-word
    unit on either side ('New_York|new_york'); <tags> go to TTS only and are skipped here."""
    raw = TTS_TAG.sub(" ", text).split()
    cores = [_core(t) for t in raw]
    opens = [c.startswith("*") for c in cores]
    closes = [c.endswith("*") and (len(c) > 1 or not o) for c, o in zip(cores, opens)]
    out, in_span = [], False
    for i, tok in enumerate(raw):
        key = in_span or "*" in tok
        if in_span:
            in_span = not closes[i]
        elif opens[i] and not closes[i]:
            for j in range(i + 1, len(raw)):  # span if a later token closes before another opens
                if opens[j]:
                    break
                if closes[j]:
                    in_span = True
                    break
        t = tok.replace("*", "")
        if "|" in t:
            disp, tts = (x.replace("_", " ") for x in t.split("|", 1))
            m = _TAIL_PUNCT.search(tts)
            if m and not _TAIL_PUNCT.search(disp):
                disp += m.group()
        else:
            disp = tts = t
        if disp:
            out.append({"display": disp, "tts": tts or disp, "key": key})
    return out


def beat_id(beat: dict, i: int) -> str:
    return beat.get("id") or f"b{i + 1}"


def script_tokens(data: dict) -> list[dict]:
    toks = []
    for bi, beat in enumerate(data.get("beats", [])):
        for t in parse_text(beat.get("text", "")):
            toks.append(dict(t, beat=beat_id(beat, bi), bi=bi))
    return toks


def tts_line(text: str) -> str:
    """One beat as sent to TTS: spoken side of each word, <tags> kept in place."""
    parts, pos = [], 0
    for m in TTS_TAG.finditer(text):
        parts += [t["tts"] for t in parse_text(text[pos:m.start()])]
        parts.append(f"<{' '.join(m.group(1).lower().split())}>")
        pos = m.end()
    return " ".join(parts + [t["tts"] for t in parse_text(text[pos:])])


def narration_text(data: dict) -> str:
    return "\n".join(x for x in (tts_line(b.get("text", "")) for b in data.get("beats", [])) if x.strip())


def display_text(text: str) -> str:
    return " ".join(t["display"] for t in parse_text(text))
