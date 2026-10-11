# Task: write the script as job.json

Write `jobs/<date>/<slug>/job.json` for one topic. Format and text syntax: `docs/job-format.md`.
Read `channel.json` for language, closing line and platform limits.

## Length and structure
- Narration 30-45 seconds: roughly 75-110 words in English (adjust for the language).
- 5-9 beats. Each beat is one or two short sentences. Short words, active voice, no filler openers.
- Hook: at most 6 words, shown as the title card. It must not claim more than the body says.
  Mark the key word with `*...*`.
- The last beat is exactly `closingLine` from channel.json, with `source: null`.

## Facts and sources
- Every beat that states a fact has `source` with name, date (first publication) and url.
- Word reports as reports: "according to X", "X reports", "the plan would". Use flat statements only for
  facts that the sources confirm. Do not turn a claim into a fact.
- Numbers: use exactly what the source says. Add a `written|spoken` alias when TTS might misread a number.
- Quotes: copy verbatim from the article or video, same word order, in context. Put the exact quote in
  `overlay.quote` and the full surrounding paragraph in `overlay.sourceText`. The check fails otherwise.
- Do not state motives, guilt, or intent of named people. No insults, no labels, no calls to action.
  For investigations or legal matters use "suspected", "charged", "according to prosecutors", and no verdicts.
- Rumours and anonymous tips are not facts. Leave them out or label them clearly.

## Visuals
- For each beat set `visual.want` (what the shot should show). Leave `assets` empty until the footage task
  has run; then fill ids, plus `cues` when a clip should start on a specific word.
- Use a `stat` overlay for a comparison of two or more numbers, a `quote` overlay for one important quote.
  At most one card per 10 seconds.

## Platform texts (`meta`)
- YouTube: title up to the limit in channel.json, ends with `#Shorts`; description of 1-3 sentences; tags.
- Instagram: caption of 1-3 sentences; hashtags within the limit.
- TikTok: caption within the limit; a few hashtags.
- No lists of sources or credits in public texts. Sources live in the video's source tags and in meta.json.
- Do not mention internal rules or the production process.

## Tone
Plain and direct, like a person explaining news to a friend. One idea per sentence. No hype words, no
rhetorical stacking, no summary sentence that repeats the previous one.

## Before you finish
Run `python pipeline/make_video.py <job_dir> --check` once and fix what it reports. Then reply with the
job path and a three-line summary: hook, main claim, open question. Do not start the build.
