# shorts-kit

Pipeline for vertical 9:16 explainer shorts (30-60 s): script -> TTS narration -> word-level captions ->
source tags, stat and quote cards -> Remotion render -> `final.mp4` plus platform texts. Python drives the stages,
`remotion/` renders. Channel settings live in `channel.json` (copy of `channel.example.json`, gitignored).

## Workflow (5 steps)
1. Topic: research subagent (`prompts/research.md`), skipping topics from `python pipeline/topics.py --days 14`.
2. Script: main session writes `jobs/<date>/<slug>/job.json` (`prompts/script.md`, format in `docs/job-format.md`).
3. Footage: one subagent (`prompts/footage.md`) fills `assets/` and the catalog via `media.py get`.
4. Build: `make_video.py <job> --check`, then `--to meta`. Look at `build/qa_sheet.png` yourself.
5. Wrap up: `qa.py --review` for false alarms, `--from cleanup --to cleanup`, short summary.

The skill `/make-video` runs all of this; `/publish` handles uploads. Stage details: `docs/pipeline.md`.

## Commands (run from repo root)
- `python pipeline/make_video.py <job_dir> --check` validate only; no TTS, no render
- `python pipeline/make_video.py <job_dir> --to meta` build everything up to meta.json
- `python pipeline/make_video.py <job_dir> --from cleanup --to cleanup`
- `python pipeline/make_video.py <job_dir> --voice narration.wav` bring your own voice
- `python pipeline/media.py get|sheet|check ...` footage download, contact sheet, framing check
- `python pipeline/qa.py <job_dir> --review "reason"` record review of QA findings
- `python pipeline/topics.py --days 14` recent topics
- `python -m unittest discover -s pipeline/tests` offline tests
Always pass `--to`. Exit codes: 0 ok, 1 stage/check error, 2 invalid job.json, 3 unreviewed QA findings.

## Hard rules
- One new topic per video. Do not remake a job that exists unless there is a genuinely new development.
- Every fact has a source (name, date, url) shown on screen as a source tag. Reports are worded as reports.
- Quotes are verbatim and must appear in `sourceText`, in context.
- No defamation: no verdicts on named people, no insults, no implied accusations. Say "according to ...".
- Every footage item has url, rights and credit in the catalog. Illustrative footage is labelled.
- No Content ID evasion: no mirroring, speed or pitch tricks, no covering logos or watermarks.
- Framing: fixed zoom <= 1.12x, total upscale <= 2.0x, 720p sources only without zoom, nothing below 720p.
  No letterbox, no blurred fill. Main subject fully visible.
- Never publish or schedule anything without the user's explicit approval for that video in the current chat.
- Never type passwords, verification codes or API keys into any page. Keys are read from env or `.env` only.
- If TTS quota is exhausted, stop and report. Do not switch models unless `tts.allowFallback` is true.

## Style for work in this repo
Keep sessions short: no extra audit or risk agents, no repeated verification loops. Read docs only when needed.
Delete temporary files when a job is done (`cleanup`). Do not edit `channel.example.json` for personal settings.

## Docs
`docs/job-format.md` job.json and text syntax; `docs/pipeline.md` stages and CLI; `prompts/` subagent tasks;
`.claude/skills/` skills; `README.md` setup.
