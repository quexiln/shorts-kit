import React, {useMemo} from 'react';
import {Audio, Sequence, useVideoConfig} from 'remotion';
import {resolveSrc} from './Shots';
import {toF} from './plan';
import type {VideoProps, Word} from './schema';

// Narration, a music bed ducked under the voice (from word timings) and sound effects.

const MUSIC_BED = 0.16; // music level when nobody speaks (if music.volume is not set)
const DUCK = 0.3; // music drops to this fraction while speaking
const PRE = 0.12; // start ducking this early (s)
const POST = 0.35; // release after speech ends (s)
const JOIN = 0.45; // silences shorter than this do not release the duck (s)

type Range = {a: number; b: number};

export const speechRanges = (words: Word[]): Range[] => {
  const out: Range[] = [];
  for (const w of [...words].sort((x, y) => x.start - y.start)) {
    const last = out[out.length - 1];
    if (last && w.start - last.b <= JOIN) last.b = Math.max(last.b, w.end);
    else out.push({a: w.start, b: w.end});
  }
  return out;
};

/** 0 = no speech, 1 = fully ducked; linear ramps around each range. */
export const duckAmount = (t: number, ranges: Range[]): number => {
  let d = 0;
  for (const r of ranges) {
    if (t < r.a - PRE || t > r.b + POST) continue;
    const k = t < r.a ? (t - (r.a - PRE)) / PRE : t > r.b ? 1 - (t - r.b) / POST : 1;
    d = Math.max(d, k);
    if (d >= 1) break;
  }
  return d;
};

export const SoundLayer: React.FC<{p: VideoProps}> = ({p}) => {
  const {fps, durationInFrames} = useVideoConfig();
  const ranges = useMemo(() => speechRanges(p.words), [p.words]);
  const bed = p.music?.volume ?? MUSIC_BED;
  const musicVolume = (f: number) => {
    const t = f / fps;
    const fade = Math.min(1, t / 0.4, (durationInFrames - f) / fps);
    return Math.max(0, bed * (1 - (1 - DUCK) * duckAmount(t, ranges)) * fade);
  };
  return (
    <>
      <Audio src={resolveSrc(p.voice.src)} volume={p.voice.volume ?? 1} name="voice" />
      {p.music ? <Audio src={resolveSrc(p.music.src)} loop loopVolumeCurveBehavior="extend" volume={musicVolume} name="music" /> : null}
      {(p.sfx ?? []).map((s, i) => {
        const from = Math.max(0, toF(s.at));
        return from < durationInFrames ? (
          <Sequence key={i} from={from} layout="none" name={`sfx ${i + 1}`}>
            <Audio src={resolveSrc(s.src)} volume={s.volume ?? 1} />
          </Sequence>
        ) : null;
      })}
    </>
  );
};
