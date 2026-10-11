# job.json format

A job lives in `jobs/<YYYY-MM-DD>/<slug>/` and contains `job.json` and an `assets/` folder
(`assets/catalog.json` plus media files). Build output goes to `build/`, final files to the job root.

## Example
A complete job is in `examples/demo/job.json` (four beats, a stat card, a quote card, a cue and a pause tag).
Shortened:

```json
{
  "slug": "library-hours",
  "date": "2026-10-08",
  "hook": {"title": "Open until *midnight*", "until": "sentence"},
  "beats": [
    {"id": "b1",
     "text": "The city library now stays open until *midnight*. The change starts *Monday*.",
     "source": {"name": "City Gazette", "date": "2026-10-07", "url": "https://example.com/library-hours"},
     "visual": {"want": "Library exterior at night", "assets": ["v01", "v02"], "cues": {"v02": "Monday"}},
     "overlay": null},
    {"id": "b2",
     "text": "Visits rose *40%|forty_percent* last year, to about one hundred sixty-eight thousand.",
     "source": {"name": "Library annual report", "date": "2026-09-30", "url": "https://example.com/annual-report"},
     "visual": {"want": "Busy reading room", "assets": ["v03"]},
     "overlay": {"type": "stat", "title": "Yearly visits", "unit": "k",
                 "items": [{"label": "2025", "value": 120}, {"label": "2026", "value": 168, "highlight": true}],
                 "until": "about"}},
    {"id": "b4", "text": "Late shifts are funded until *next year*. <short pause> What do you think?",
     "source": {"name": "City Gazette", "date": "2026-10-07", "url": "https://example.com/library-hours"},
     "visual": {"assets": ["v01", "v03"]}, "overlay": null}
  ],
  "tts": {"style": "optional per-job override"},
  "meta": {
    "youtube": {"title": "The city library now stays open until midnight #Shorts", "description": "...",
                "tags": ["library"]},
    "instagram": {"caption": "...", "hashtags": ["#library", "#citynews"]},
    "tiktok": {"caption": "...", "hashtags": ["#library"]}
  }
}
```

## Top-level fields
| Field | Required | Meaning |
|---|---|---|
| `slug` | yes | Folder name; lowercase letters, digits, hyphens |
| `date` | yes | `YYYY-MM-DD` |
| `hook` | yes | Title card at the top: `title` (text syntax below), `until` |
| `beats` | yes | Ordered list; narration is the beats' `text` joined in order |
| `tts` | no | Per-job overrides of channel `tts` (for example `style`) |
| `meta` | yes | Platform texts, validated against limits in `channel.json` |

`hook.until`: `"sentence"` (0.2 s after the first sentence of beat 1 ends), a word of the first beat (0.2 s after
that word), or seconds. Default: end of the first beat. Always clamped to 1.8-4 s. Optional `tts` keys: `style`,
`voice`, `tempo` (0.9-1.12), `gapCap` (max pause, default 0.3 s); `edit` keys: `shotMin`, `shotMax`, `shotTarget`.

## Beats
- `id`: unique, e.g. `b1`.
- `text`: narration text with the syntax below. Narration must end with `closingLine` from `channel.json`
  for the outro to start on it.
- `source`: `{name, date, url}` or `null`. Every beat that states a fact needs one; `date` is the first
  publication date. A source tag "Name · date" is shown during the beat. `null` only for non-factual beats
  (the closing line).
- `visual.want`: free text, what the shot should show (for the footage task).
- `visual.assets`: catalog ids in order; the beat's duration is split among them. Long beats are cut into
  shots of about 1.2-2.5 s at word boundaries.
- `visual.cues`: `{assetId: word}`; that asset starts as the word is spoken (may slide into the word if the
  clip is short). Assets without a cue fill the rest.
- If a beat's own footage runs out, catalog items without `beat` and the previous beat's assets are used.
- `overlay`: `null`, a `stat` card or a `quote` card. A stat card starts at the beat start; a quote card starts
  when the quote's first words are spoken (if there is at least 1.2 s before them). Cards last 1.5-4.5 s.
  - `until`: a word of the beat; the card ends just before that word is spoken (default: end of beat).

### Captions
Captions are off while a card is shown, during the hook if its title mostly repeats the first beat, and from
the outro on when the narration ends with `closingLine` (the outro shows that line instead).

### stat overlay
`{"type":"stat","title","unit"?,"items":[{"label","value","highlight"?}],"until"?}`. 2-5 items, horizontal bars.

### quote overlay
`{"type":"quote","quote","name","role"?,"highlight"?,"sourceText","until"?}`.
`quote` must appear verbatim inside `sourceText` (same words in the same order; case and punctuation are ignored). The check fails otherwise.
`highlight` is a word inside the quote shown in the accent colour.

## Text syntax (beat text, hook title)
- `*word*`: key word, highlighted in captions.
- `written|spoken`: captions show `written`, TTS reads `spoken`. Example: `*40%|forty_percent*`.
- Underscore `_` joins words into one unit on either side: `New_York|new_york`. Writing `New York|new_york`
  makes TTS say "New" twice; `--check` reports it.
- Numbers without an alias are spelled out for alignment (built-in for Turkish, `num2words` for other
  languages if installed, else kept as digits). The `|` alias is the reliable way.
- `<short pause>` and similar tags go to TTS only; captions never show them.
- In the hook, `\n` forces a line break.
- Do not put style directions or stage notes in the text; the narrator reads the text as written.
  Style goes in `tts.style`.

## Media catalog (`assets/catalog.json`)
Written by `python pipeline/media.py get`. A list of:

| Field | Meaning |
|---|---|
| `id` | `v01`, `v02`, ... referenced from beats |
| `file` | path relative to `assets/` |
| `kind` | `video` or `image` |
| `srcUrl` | original URL |
| `srcStart`, `srcEnd` | downloaded range in the source video (seconds) |
| `from`, `to` | usable range in the downloaded FILE (seconds; pick from `media.py sheet`). Default: whole file |
| `maxDur` | optional: longest single shot from this item (seconds) |
| `beat` | optional: beat id this item belongs to; items without `beat` are general fallback footage |
| `width`, `height` | pixels of the file |
| `rights` | licence or basis, free text; required (as are `srcUrl` and `credit`) |
| `credit` | author or channel; shown with the shot |
| `illustrative` | true: shot is labelled (default text `ILLUSTRATIVE`, set `labels.illustrative` in channel.json) |
| `focusX`, `focusY` | 0-1 crop focus for the 9:16 cover crop |
| `zoom` | optional fixed zoom factor (number, at most 1.12); the clip is pre-cropped around the focus |
| `motion` | optional `none`, `punch` or `slow`. Default: images `slow`; video `punch` when a key word lands on the cut |

Framing limits enforced by `--check`, `media.py check` and the timeline stage: fixed zoom <= 1.12x; sources
whose short side is below 720 px are rejected; above 2.0x total upscale (cover x zoom, e.g. 1280x720) zoom is
an error and no zoom is a warning (prefer 1080p). Cover crop only: no letterbox, no blurred fill.
Source audio is always muted.

## Platform texts (`meta`)
Limits come from `channel.json` `platforms`: YouTube title length (default 100, must end with `#Shorts`),
Instagram hashtag count (default 5), TikTok caption length (default 2200). Phrases listed in
`bannedPublicPhrases` must not appear. `meta.json` written by the build adds sources and credits for your
records; keep them out of public captions.
