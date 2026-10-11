"""Narration -> assets/voice.wav.
Gemini TTS (3.x: interactions API, text read verbatim, style in speech_metadata) -> whisper leak check
-> silence capping -> tempo -> loudness -> 48 kHz mono wav. Quota exhausted -> stop, never switch model silently.
`--voice FILE` (bring your own narration) goes through use_voice_file instead."""
from __future__ import annotations

import base64
import io
import re
import time
import wave
from pathlib import Path

from common import Job, PipelineError, db_frames, ffprobe_duration, file_sig, load_json, log, narration_text, run, \
    save_json, script_tokens, sha1

API_V = 3            # bump when the request format changes (invalidates cached raw takes)
PROC_V = 1           # bump when post-processing changes (raw take is reused, no API call)
GAP_CAP = 0.30       # inner pauses are capped to this (s)
LEAD_SEC = 0.10      # silence before speech
VOICE_LUFS = -16
MIN_MATCH = 0.75
MAX_STYLE = 160


class QuotaExhausted(RuntimeError):
    pass


def one_sentence(style: str | None) -> str:
    """The style note must be one short sentence; longer notes tend to be read aloud."""
    s = re.split(r"(?<=[.!?])\s+", (style or "").strip().replace("\n", " "))[0].rstrip(" .:;!")
    return s[:MAX_STYLE].rsplit(" ", 1)[0] if len(s) > MAX_STYLE else s


def interactions_input(text: str, style: str) -> list[dict]:
    content = {"type": "text", "text": text}
    if style:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    return [{"type": "user_input", "content": [content]}]


def write_audio(data, mime: str | None, path) -> None:
    """WAV bytes as-is; raw L16/PCM wrapped as mono 16-bit wav (rate from mime, default 24 kHz)."""
    if isinstance(data, str):
        data = base64.b64decode(data)
    if not data:
        raise RuntimeError("empty audio response")
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as w:
            w.getnframes()
        Path(path).write_bytes(data)
        return
    m = re.search(r"rate=(\d+)", mime or "")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(m.group(1)) if m else 24000)
        w.writeframes(data)


def synth(client, model: str, text: str, style: str, voice: str, path, key=None, tries: int = 3) -> None:
    """One model; retries on 429 (rate) and transient errors. Daily quota -> QuotaExhausted."""
    from keys import scrub
    last = None
    for attempt in range(tries):
        try:
            if model.startswith("gemini-3"):
                it = client.interactions.create(model=model, input=interactions_input(text, style),
                                                response_format={"type": "audio"},
                                                generation_config={"speech_config": [{"voice": voice}]})
                audio = getattr(it, "output_audio", None)
                if audio is None:
                    raise RuntimeError("empty audio response (no output_audio)")
                return write_audio(getattr(audio, "data", None), getattr(audio, "mime_type", None), path)
            from google.genai import types  # 2.x models: style is prepended to the text
            r = client.models.generate_content(
                model=model, contents=f"{style}:\n{text}" if style else text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
            part = r.candidates[0].content.parts[0].inline_data
            return write_audio(part and part.data, part and part.mime_type, path)
        except Exception as e:  # noqa: BLE001
            msg, code = scrub(str(e), key), getattr(e, "code", None)
            last = RuntimeError(f"{model}: {msg[:300]}")
            if code == 429 or "RESOURCE_EXHAUSTED" in msg:
                if re.search(r"PerDay|per day|daily", msg, re.I):
                    raise QuotaExhausted(f"{model}: daily quota exhausted") from None
                last = QuotaExhausted(f"{model}: {msg[:300]}")
                m = re.search(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s", msg, re.I)
                d = min(65.0, (float(m.group(1)) if m else 6 * 2 ** attempt) + 1.5)
                log.warning(f"  {model}: rate limited, waiting {d:.0f} s")
                time.sleep(d)
            elif (isinstance(code, int) and code >= 500) or "empty audio" in msg or \
                    isinstance(e, (AttributeError, IndexError, TypeError)):
                time.sleep(3 * (attempt + 1))
            else:
                raise last from None
    raise last


def cap_silences(src, dst, gap_cap=GAP_CAP, tail_keep=0.08) -> dict:
    """Drop head/tail silence and shorten inner pauses to gap_cap (cut from the middle, edges kept)."""
    import numpy as np
    with wave.open(str(src), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise PipelineError("cap_silences: 16-bit wav expected")
    x = np.frombuffer(raw, np.int16).reshape(-1, ch).astype(np.float32).mean(1) / 32768
    hop = max(1, int(sr * 0.01))
    db = db_frames(x, hop)
    n = len(db)
    if n < 10:
        raise PipelineError("cap_silences: audio too short")
    thr = min(-42.0, float(np.percentile(db, 90)) - 32)
    loud = np.flatnonzero(db >= thr)
    if not len(loud):
        raise PipelineError("cap_silences: no speech found")
    first, last = int(loud[0]), int(loud[-1]) + 1
    keep = max(1, int(round(gap_cap / 0.01)))
    segs, removed, i, cur = [], 0, first, first
    while i < last:
        if db[i] < thr:
            j = i
            while j < last and db[j] < thr:
                j += 1
            if j - i > keep:
                segs.append((cur, i + keep // 2))
                cur = j - (keep - keep // 2)
                removed += j - i - keep
            i = j
        else:
            i += 1
    segs.append((cur, min(n, last + int(round(tail_keep / 0.01)))))
    fade, parts = max(1, int(sr * 0.004)), []
    for a, b in segs:
        p = x[a * hop:b * hop].copy()
        if len(p) > 2 * fade:
            p[:fade] *= np.linspace(0, 1, fade)
            p[-fade:] *= np.linspace(1, 0, fade)
        parts.append(p)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(np.concatenate(parts), -1, 1) * 32767).astype(np.int16).tobytes())
    return {"removed_sec": round(removed * 0.01, 2), "gaps_capped": len(segs) - 1}


def process(raw, out, cut_start=None, cut_end=None, tempo=1.0, gap_cap=GAP_CAP) -> dict:
    """Leak cut + silence capping + tempo + loudness -> 48 kHz mono 16-bit wav."""
    out = Path(out)
    t1, t2 = out.with_name("_cut.tmp.wav"), out.with_name("_cap.tmp.wav")
    af = "anull"
    if cut_start or cut_end:
        af = f"atrim=start={cut_start or 0}" + (f":end={cut_end}" if cut_end else "") + ",asetpts=PTS-STARTPTS," \
             "afade=t=in:d=0.02"
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af", af, "-ac", "1", "-c:a", "pcm_s16le", t1])
        st = cap_silences(t1, t2, gap_cap)
        af = ([f"atempo={tempo:.3f}"] if abs(tempo - 1) > 1e-3 else []) + \
             [f"loudnorm=I={VOICE_LUFS}:TP=-1.5:LRA=11", "aresample=48000", f"adelay={int(LEAD_SEC * 1000)}"]
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", t2, "-af", ",".join(af), "-ac", "1", "-ar", "48000",
             "-c:a", "pcm_s16le", out])
    finally:
        for t in (t1, t2):
            t.unlink(missing_ok=True)
    return st


def use_voice_file(job: Job, src) -> dict:
    """Bring your own narration: convert + loudness-normalise to assets/voice.wav (pacing untouched)."""
    src = Path(src).resolve()
    if not src.exists():
        raise PipelineError(f"--voice file not found: {src}")
    info_p, out = job.b("tts_info.json"), job.a("voice.wav")
    sig = sha1("voice-file", file_sig(src), VOICE_LUFS)
    if info_p.exists() and out.exists() and load_json(info_p).get("sig") == sig:
        log.info("tts: --voice file unchanged")
        return load_json(info_p)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af",
         f"loudnorm=I={VOICE_LUFS}:TP=-1.5:LRA=11,aresample=48000", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
         out])
    info = {"sig": sig, "model": "voice-file", "source": src.name, "duration": round(ffprobe_duration(out), 3)}
    save_json(info_p, info)
    log.info(f"tts: --voice {src.name} -> assets/voice.wav ({info['duration']:.2f} s)")
    return info


def run_tts(job: Job, force: bool = False, client=None) -> dict:
    """client: injectable fake for tests (no key is read then)."""
    from align import detect_leak, transcribe
    from keys import get_key, scrub
    text = narration_text(job.data)
    if not text.strip():
        raise PipelineError("narration text is empty")
    style = one_sentence(job.tts_opt("style"))
    voice = job.tts_opt("voice", "Orus")
    tempo = min(1.12, max(0.9, float(job.tts_opt("tempo", 1.0))))
    gap_cap = float(job.tts_opt("gapCap", GAP_CAP))
    primary = job.tts_opt("model", "gemini-3.8-flash-tts")
    allow = job.tts_opt("allowFallback", False) is True
    models = [primary] + (list(job.tts_opt("fallbackModels") or []) if allow else [])
    synth_sig = sha1(API_V, text, style, voice, primary)
    sig = sha1(synth_sig, tempo, gap_cap, LEAD_SEC, VOICE_LUFS, PROC_V)
    info_p, out = job.b("tts_info.json"), job.a("voice.wav")
    info = load_json(info_p) if info_p.exists() and not force else {}
    if info.get("sig") == sig and out.exists():
        log.info(f"tts: up to date ({info.get('model')})")
        return info
    job.assets.mkdir(parents=True, exist_ok=True)
    job.build.mkdir(parents=True, exist_ok=True)
    raw_p = job.b(info.get("raw") or "-")
    if info.get("synth_sig") == synth_sig and raw_p.is_file():  # only post-processing changed: no API call
        info.update(sig=sig, tempo=tempo, silence=process(raw_p, out, info.get("cut_start"), info.get("cut_end"),
                                                          tempo, gap_cap), duration=round(ffprobe_duration(out), 3))
        save_json(info_p, info)
        log.info(f"tts: reprocessed cached take -> voice.wav ({info['duration']:.2f} s)")
        return info
    key = None
    if client is None:
        key = get_key()
        from google import genai
        client = genai.Client(api_key=key)
    tokens, attempts, best = script_tokens(job.data), [], None
    wsize = (job.channel.get("whisper") or {}).get("check_model", "small")
    for model in models:
        for _ in range(2):  # one quality retry per model
            raw = job.b(f"tts_raw_{len(attempts)}.wav")
            try:
                synth(client, model, text, style, voice, raw, key)
            except Exception as e:  # noqa: BLE001
                msg = scrub(str(e), key)
                attempts.append({"model": model, "ok": False, "error": msg[:300]})
                if best is None and not (allow and model != models[-1]):
                    save_json(job.b("tts_attempts.json"), attempts)
                    if isinstance(e, QuotaExhausted):
                        raise PipelineError(f"TTS quota exhausted ({model}). Stopping; no fallback model is used "
                                            f"unless tts.allowFallback is true.") from None
                    raise PipelineError(f"TTS failed ({msg[:200]})") from None
                break
            leak = detect_leak(tokens, transcribe(raw, wsize, job.lang), job.lang)
            rec = {"model": model, "raw": raw.name, "match": round(leak["match"], 3), "tail_ok": leak["tail_ok"],
                   "cut_start": leak["cut_start"], "cut_end": leak["cut_end"], "lead_words": leak["lead_words"][:20]}
            attempts.append(rec)
            if leak["cut_start"]:
                log.warning(f"  leak: {len(leak['lead_words'])} extra words before the script are cut")
            score = leak["match"] + (0.2 if leak["tail_ok"] else 0)
            if best is None or score > best[0]:
                best = (score, rec)
            if leak["match"] >= MIN_MATCH and leak["tail_ok"]:
                break
            log.warning(f"  {model}: weak take (match {leak['match']:.0%}), retrying once")
        if best and best[1]["match"] >= MIN_MATCH and best[1]["tail_ok"]:
            break
    if not best:
        raise PipelineError("TTS produced no audio: " + "; ".join(a.get("error", "")[:100] for a in attempts))
    rec = best[1]
    if rec["match"] < MIN_MATCH or not rec["tail_ok"]:
        log.warning(f"tts: best take is weak (match {rec['match']:.0%}); listen before publishing")
    st = process(job.b(rec["raw"]), out, rec["cut_start"], rec["cut_end"], tempo, gap_cap)
    info = {"sig": sig, "synth_sig": synth_sig, "model": rec["model"], "fallback": rec["model"] != primary,
            "voice": voice, "style": style, "tempo": tempo, "silence": st, "cut_start": rec["cut_start"],
            "cut_end": rec["cut_end"], "raw": rec["raw"], "duration": round(ffprobe_duration(out), 3),
            "attempts": attempts}
    save_json(info_p, info)
    log.info(f"tts: {rec['model']} ({voice}) -> voice.wav {info['duration']:.2f} s, match {rec['match']:.0%}")
    return info
