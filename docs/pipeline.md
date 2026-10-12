# Pipeline

Run from the repo root:

```
python pipeline/make_video.py <job_dir> [--check] [--from STAGE] [--to STAGE] [--keep] [--force] [--voice FILE]
```

## Stages
`tts -> align -> sfx -> timeline -> render -> qa -> meta -> cleanup`

| Stage | What it does | Output |
|---|---|---|
| tts | Gemini TTS; cuts text read before/after the script, caps pauses, tempo, loudness | `assets/voice.wav` |
| align | faster-whisper word timings mapped to the script; boundary fixes; trims noise after the last word | `build/words.json` |
| sfx | synthesised whoosh/pop/tick; copies the optional `channel.json` `music` file (path or `{src, volume}`) | `assets/sfx/*` |
| timeline | beats to shots, source tags, cards, hook, outro; writes Remotion props | `build/props.json` |
| render | Remotion render (`build/render.mp4`), then two-pass loudnorm to -14 LUFS | `final.mp4` |
| qa | contact sheet (1 fps, safe-area lines), black frame and edge-line checks | `build/qa_sheet.png`, `build/qa_report.json` |
| meta | validates platform texts, collects sources (every beat per source) and credits | `meta.json`, `sources.md` |
| cleanup | copies small files to `keep/`, deletes `assets/` and `build/` | `keep/` |

Always give `--to`. Typical runs: `--to meta`, then `--from cleanup --to cleanup`.

## Flags
- `--check`: validate `job.json`, platform text, catalog and framing. No TTS, no render. Exit 1 on errors.
- `--from`, `--to`: first and last stage to run.
- `--keep`: skip deletion in cleanup.
- `--force`: ignore caches and rerun the selected stages. `--from S` also forces S and later stages.
- `--voice FILE`: use your own narration (WAV or MP3) instead of Gemini. It is converted to 48 kHz mono and
  loudness-normalised into `assets/voice.wav`; pauses and tempo are left as recorded. Alignment still runs,
  so the recording must follow the script text.
- Cleaned job (no `assets/`): `tts` to `qa` refuse to run unless `--force` is given. With `--force` the files in
  `keep/` (catalog, voice, words, props, reports) are restored; footage must be downloaded again.
  `meta` runs on a cleaned job without `--force` and keeps the recorded sources, media and video info.

## Exit codes
0 ok; 1 stage or check error; 2 `job.json` invalid; 3 QA findings not yet reviewed.

## Caching
Each stage stores a signature of its inputs in `build/<stage>_info.json`. If inputs are unchanged the stage
is skipped, so editing a caption or card does not repeat TTS. Changing beat text, voice, style or tempo
invalidates `tts` and everything after it. Changing only catalog entries or overlays reruns from `timeline`.

## TTS and quota
The key is read from env `GEMINI_API_KEY` (or `GEMINI`), or the `GEMINI_API_KEY=` line of `.env` at the repo
root. It is never printed. Text is sent verbatim; the style goes in `speech_metadata.style`. If the quota is
exhausted the run stops with a message (exit 1). No other model is tried unless `tts.allowFallback` is true and
`tts.fallbackModels` lists one. Use `--voice` to continue without the API.

## Alignment
Language comes from `channel.json` (`whisper.model`, default `medium`). Numbers are spelled out for matching:
built-in speller for Turkish, `num2words` for other languages if installed, else left as digits; `%`, decimal
and thousands separators follow the language. Prefer `written|spoken` aliases for numbers. Upper/lower case
is locale-aware for `tr` and `az` (dotted and dotless i). Words are matched to the script with difflib; small boundary
errors are corrected automatically.

## QA and review
`qa` writes `build/qa_sheet.png` (one frame per second with safe-area lines) and `build/qa_report.json`
(black frames, letterbox bars, hard edge lines near the crop border). A flat black band with a straight edge is reported as "black bar"; one with an uneven edge (dark footage on black) as "dark band ... letterbox or dark footage?", so look at the frame. A finding on a single sampled frame is re-checked at full resolution +-0.2 s and dropped if the neighbours are clean (transition frame). There is no automatic vision model: look at the sheet
yourself. For each finding decide whether it is real. False alarm:

```
python pipeline/qa.py <job_dir> --review "reason"
```

This records the review in the report (exit 0); without `--review` it prints the report (exit 3 if unreviewed). Real finding: change the clip,
time range, focus or zoom in `job.json` or the catalog and rebuild from `timeline`.
Cleanup refuses to run while findings are unreviewed (exit 3).

## Other tools
- `python pipeline/media.py get <url> --job <job_dir> --start S --end E --rights R --credit C [--illustrative]
  [--id ID] [--image]`: download a time range (yt-dlp <= 1080p; direct .mp4/.mov/.webm URLs are cut with ffmpeg) or an image (.jpg/.png/.webp URLs are detected), and add/update the catalog
  entry (ids `v01`, `p01`... by default). Then set `from`/`to`, `focusX`/`focusY` by hand.
- `python pipeline/media.py sheet <job_dir> [--id ID] [--fx F] [--fy F] [--zoom Z] [--from A] [--to B]`:
  `build/sheets/<id>.png`, timestamped frames (file time) with the 9:16 crop window and a focusX ruler.
- `python pipeline/media.py check <job_dir>`: files, required fields, from/to, framing limits, beat links.
- `python pipeline/topics.py [--days 14] [--all] [--q WORD] [--json]`: jobs under `jobs/` by date, slug, hook.
- `python pipeline/keys.py`: says whether and where the Gemini key was found (never prints it).
- Tests: `python -m unittest discover -s pipeline/tests` (offline: no network, no TTS, no render).

## Remotion
`remotion/` is a normal Remotion project (composition `Video`, 1080x1920, 30 fps). The render stage runs
`npx remotion render src/index.ts Video <out> --props=<props.json> --public-dir=<job assets>`. Props are
defined in `remotion/src/schema.ts`. Preview with `cd remotion && npx remotion studio`.

## Cleanup
Cleanup keeps `job.json`, `final.mp4`, `meta.json`, `sources.md` and small files in `keep/`; it removes
`assets/` and `build/`. Remove `final.mp4` and `keep/` yourself after publishing if you want the disk back.

## Timeline rules
Cuts sit on the 30 fps grid, shots are contiguous, long beats split into 1.2-2.5 s shots at word
boundaries, a clip a few frames short holds its last frame (<= 0.25 s). A still holds up to 4 s (slow zoom)
instead of alternating with another asset; no A-B-A-B alternation, at most two shots in a row from one source
within a beat. Video continues where it left off; a range already shown is replayed only when all footage is used
up (warning). Shots under 1 s merge into a neighbour. A beat with less footage than time logs
`b3: only 1 asset for 9.4 s - add footage`. The outro starts at the closing line if
the narration ends with `closingLine`, else 0.05 s after the last word, and lasts 2.5 s. Source tags appear at
beat start (after the hook), do not overlap, and last 1.6-3.2 s. Card and caption rules: `docs/job-format.md`.
