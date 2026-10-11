---
name: make-video
description: Produces one vertical explainer short from a new topic with shorts-kit. Covers topic research, script and job.json, footage, build (TTS, captions, render), QA review, cleanup. Use when the user asks for a video, a short, or "make a video about X". Does not publish anything; use the publish skill for that.
---

# make-video

Goal: one finished `final.mp4` plus `meta.json` for one new topic, with the fewest steps and tokens.
Run all commands from the repo root. Hard rules are in `CLAUDE.md`; do not restate them to subagents beyond
what their prompt files say.

## 0. Preconditions
- `channel.json` exists (else copy `channel.example.json` and tell the user). Handle and language come from it.
- API key available (`GEMINI_API_KEY` in env or `.env`). Never print it. If missing, ask the user to set it,
  or use `--voice FILE`.
- If the user gave a topic, skip step 1. If a job for it already exists in `python pipeline/topics.py --days 14`,
  tell the user and stop unless there is a new development.
- If two or more finished jobs are unpublished, mention it once and continue only if the user wants more.

## 1. Topic
Run `python pipeline/topics.py --days 14`. Spawn one subagent with the task text from `prompts/research.md`,
passing the niche (from the user or `channel.json`) and the topics list. It returns 3 candidates, each with 2+
dated sources. Pick the best one yourself (fresh, easy to show, sources agree). If the user wants to choose,
show the three in one short list and wait.

## 2. Script and job.json
Do this in the main session, following `prompts/script.md` and `docs/job-format.md`.
- Create `jobs/<YYYY-MM-DD>/<slug>/job.json` (and `assets/` next to it).
- 30-45 s of narration, hook of at most 6 words, closing line from `channel.json`.
- Every beat with a fact has `source`. Reports are worded as reports; only confirmed facts are stated flat.
- Leave `visual.assets` empty until footage exists; fill `visual.want` for each beat.
Do not start a separate risk or legal review agent. Apply the script prompt's checklist while writing.

## 3. Footage
Spawn one subagent with `prompts/footage.md`, giving it the job directory. It downloads with
`python pipeline/media.py get ...`, writes `assets/catalog.json`, and returns the asset ids per beat with
one line each on rights and framing. Then put the ids into `visual.assets` (and `cues` where a shot should
change on a word). Prefer real event footage; illustrative stock is allowed and is marked on screen.

## 4. Check
`python pipeline/make_video.py jobs/<date>/<slug> --check`
Fix every error it reports (job syntax, quote not in `sourceText`, platform text limits, framing). Run it again
until it exits 0. Do not run it more than needed.

## 5. Build
`python pipeline/make_video.py jobs/<date>/<slug> --to meta`
- Stages are cached; re-running does not repeat TTS if the text is unchanged.
- TTS quota exhausted: stop and report to the user. Do not switch models or retry in a loop.
- Exit 3 means QA findings need review (step 6).

## 6. Look at the result
Open `build/qa_sheet.png` with the Read tool and check, in this order:
1. Main subject visible and roughly centred; no empty ceiling, wall or sky filling more than half the frame.
2. No black bars, no blur fill, no visible logos or watermarks that conflict with rights.
3. Source tags, hook, cards and captions sit inside the safe area and do not overlap.
4. Shot changes make sense against the script.

Also read `build/qa_report.json`. Judge each finding critically. If it is a false alarm:
`python pipeline/qa.py jobs/<date>/<slug> --review "why it is a false alarm"`.
If it is real, fix the cause (different clip or time range, focus, zoom, overlay timing) and rebuild with
`--from timeline --to meta`. One fix round is normally enough; if the problem persists, tell the user.

## 7. Cleanup and summary
`python pipeline/make_video.py jobs/<date>/<slug> --from cleanup --to cleanup`
Reply with at most 8 lines: title, length, path of `final.mp4`, the sources used, any illustrative or archive
footage, anything the user should decide. Offer to publish; do not start publishing.

## Do not
- Add audit, risk or "double check" agents or loops.
- Re-verify simple facts that the sources already state.
- Publish, schedule or upload anything.
- Leave `assets/` or `build/` behind after cleanup.
