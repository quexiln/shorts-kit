# remotion

The Remotion project that renders a job into a 1080x1920, 30 fps video. The pipeline writes
`props.json` (contract: `src/schema.ts`) and calls the render with the job's assets folder as `--public-dir`.

## Setup

```
npm install
npm run typecheck
```

## Demo

The demo props (`src/demo.ts`) use synthetic placeholder media. Generate it once (needs ffmpeg, numpy, Pillow):

```
python scripts/make_demo_media.py
```

This writes `public/demo/` (gitignored). Then:

```
npm run studio                                   # preview in the browser
npx remotion render src/index.ts Video out/demo.mp4
npx remotion still src/index.ts Video out/frame.png --frame=100
```

## Files

- `src/schema.ts` props contract shared with the Python pipeline
- `src/plan.ts` seconds to frames, shot gaps, hook/source/card conflicts
- `src/Shots.tsx` cover-cropped shots, zoom, last-frame freeze, illustrative label and credit
- `src/Captions.tsx` word-timed captions in groups of 1-3 words
- `src/Hook.tsx`, `src/Overlays.tsx`, `src/Outro.tsx` title, source tag, stat and quote cards, closing line
- `src/Sound.tsx` narration, music ducked under speech, sound effects
- `src/theme.ts` safe areas and fonts (Inter, Montserrat, Poppins, Roboto; anything else falls back to Inter)

Safe area: top 220, bottom 420, left 60, right 150 px. Shots are always cover-cropped; there is no
letterbox or blurred fill, and zoom stays at or below 1.1x.
