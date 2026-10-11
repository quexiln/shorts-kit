# Task: collect footage for a job

Fill `assets/` and `assets/catalog.json` for one job. You do not edit the script text.

## Inputs
- The job directory (`jobs/<date>/<slug>/`). Read its `job.json`: each beat has `visual.want`.
- Tools: `python pipeline/media.py get|sheet|check ...` (run `--help` for flags), WebSearch, yt-dlp via media.py.

## Priority
1. Real footage of the actual event or subject: official channels, press conferences, public broadcasters,
   agency clips that allow reuse, the organisation's own channel.
2. Archive footage of the same people or place, labelled as archive in the credit.
3. Illustrative stock (Pexels, Pixabay, Wikimedia Commons, or similar with a clear licence). Mark these
   `illustrative=true`. Choose wide, recognisable scenes; no abstract close-ups.

## Rights record (required for every item)
Each catalog entry needs `srcUrl`, `rights` (for example "CC0", "Pexels licence", "press conference, editorial
use", "unclear") and `credit` (author or channel). If rights are unclear, say so in `rights`; do not invent
a licence. Report unclear items to the caller. You are recording rights, not clearing them.

## Not allowed
- Mirroring, speed or pitch changes, cropping or covering logos, watermarks or channel bugs, or any other
  trick to avoid automatic copyright matching. If a clip has a burned-in logo, pick another clip or moment.
- Source audio: it is always muted in the render; do not look for clips because of their sound.
- Screen recordings of paywalled or private content.

## Framing limits (checked by `media.py check` and `--check`)
- Fixed zoom at most 1.12x; total upscale (cover crop x zoom) at most 2.0x.
- 720p sources only without zoom; anything below 720p is rejected.
- Main subject complete and near the centre. No frame with more than half empty ceiling, wall or sky.
- No black bars, no letterboxing, no blurred fill. Set `focusX`/`focusY` to keep the subject in a 9:16 crop.
- Pick a start and end that avoid on-screen text from the original broadcast, scoreboards, or lower thirds
  unless they belong to the story.

## Procedure
1. For each beat, find 1-2 candidates. Prefer clips of 3-8 seconds usable length.
2. `python pipeline/media.py get <url> --job <job_dir> --start <s> --end <s> ...` to download the range and
   add the catalog entry (`--rights "..." --credit "..."`, plus `--illustrative` for stock or symbolic shots).
3. `python pipeline/media.py sheet <job_dir> [--id v01]` and look at the contact sheet yourself (Read the image).
   In `assets/catalog.json` set `from`/`to` (usable range in FILE time, read off the sheet) and
   `focusX`/`focusY`; try values with `sheet --id v01 --fx 0.3`. Replace anything that fails the framing rules.
4. `python pipeline/media.py check <job_dir>` once; fix errors.

## Output
A short table: beat id | asset id | source | start-end | rights | illustrative | note. Then list beats
without good footage and what you tried. At most 40 lines. Delete any downloaded file you did not keep.
