# shorts-kit

Script-to-vertical-video pipeline for 30–60 s explainer shorts: TTS narration, word-level captions, source tags, stat/quote cards, rendered with Remotion; driven by Claude Code skills or by hand.

## Features

- Job file (`job.json`) describing beats, sources, footage and platform texts
- Narration with Gemini TTS, or bring your own voice file
- Word-level captions aligned with faster-whisper
- On-screen source tags, stat cards and quote cards; quotes are checked against the source text
- Footage catalog that records URL, rights and credit for every clip, with framing limits enforced
- Contact-sheet QA and platform text validation (YouTube Shorts, Instagram Reels, TikTok)
- Stages are cached, so re-running does not repeat TTS calls
- Claude Code skills for producing and publishing videos

## Requirements

- Python 3.11+
- Node 20+
- ffmpeg
- yt-dlp
- Optional: `num2words` for spelling out numbers during alignment (built-in for Turkish)
- A Gemini API key for TTS (not needed with `--voice`)

## Quick start

```
git clone https://github.com/quexiln/shorts-kit.git
cd shorts-kit
pip install -r requirements.txt
cd remotion && npm install && cd ..
cp channel.example.json channel.json
export GEMINI_API_KEY=...        # or put GEMINI_API_KEY=... in .env
```

Check the setup with the Remotion-only demo (synthetic media, no API key needed):

```
cd remotion
python scripts/make_demo_media.py
npx remotion render src/index.ts Video out/demo.mp4
cd ..
```

Run the full pipeline on the demo job (creates `jobs/<today>/library-hours/` with synthetic footage):

```
python examples/demo/setup_demo.py
python pipeline/make_video.py jobs/<today>/library-hours --check
python pipeline/make_video.py jobs/<today>/library-hours --to meta                    # needs GEMINI_API_KEY
python pipeline/make_video.py jobs/<today>/library-hours --to meta --voice your.wav   # or your own narration
```

With `--voice`, the recording should read the demo script (alignment maps it to the text).

Then create your first job in `jobs/<YYYY-MM-DD>/<slug>/job.json` (see `docs/job-format.md`), add footage with
`media.py get`, and run:

```
python pipeline/make_video.py jobs/<date>/<slug> --check
python pipeline/make_video.py jobs/<date>/<slug> --to meta
```

## Troubleshooting

If npm reports that the esbuild postinstall script was not run: `npm approve-scripts esbuild` in `remotion/`.

## Usage with Claude Code

Open the repo in Claude Code. `CLAUDE.md` and the skills are loaded automatically.

- `/make-video <topic or niche>`: research, script, footage, build and QA for one video.
- `/publish`: upload a finished video to YouTube Shorts, Instagram Reels and TikTok after your explicit
  approval for that video.

## Manual usage

1. Write `job.json` following `docs/job-format.md`.
2. Download footage: `python pipeline/media.py get <url> --job <job_dir> --start 12 --end 18 --rights "..." --credit "..."`
3. Validate: `python pipeline/make_video.py <job_dir> --check`
4. Build: `python pipeline/make_video.py <job_dir> --to meta`; view `build/qa_sheet.png`.
5. Review QA findings if any: `python pipeline/qa.py <job_dir> --review "reason"`.
6. Clean up: `python pipeline/make_video.py <job_dir> --from cleanup --to cleanup`.

With your own narration: add `--voice narration.wav`. See `docs/pipeline.md` for stages, flags and exit codes.

## Project layout

```
.claude/skills/    make-video and publish skills
docs/              job format, pipeline reference
examples/demo/     demo job.json and setup_demo.py (synthetic footage)
jobs/              your jobs (gitignored)
pipeline/          Python stages, media and QA tools, tests
prompts/           subagent task texts (research, script, footage)
remotion/          Remotion project (composition, captions, cards)
channel.example.json   channel settings template
```

## Responsible use

You are responsible for the rights to the footage you use and for following the rules of each platform you
publish to. shorts-kit records sources, rights and credits in the catalog and in `meta.json`, but it does not
clear rights or check licences. Source attribution on screen is not a substitute for permission. Check the
terms of any site you download from.

## License

MIT. See `LICENSE`.
