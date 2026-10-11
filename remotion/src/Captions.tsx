import React, {useMemo} from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {CAPTION_Y, SAFE_W, SAFE_X0, alpha} from './theme';
import {inAny, type Span} from './plan';
import type {Theme, Word} from './schema';

// Word-timed captions in short groups of 1-3 words.

export type Chunk = {index: number; words: Word[]; start: number; end: number};

const MAX_WORDS = 3;
const MAX_CHARS = 18;
const MAX_GAP = 0.45; // a pause this long starts a new group (s)
const LINGER = 0.35; // last group stays this long after its last word (s)
const SENT_END = /[.!?…,;:]$/;
const FONT_MAX = 84;
const CHAR_EM = 0.6; // rough average glyph width of a bold sans, in em

// Shown form: trailing comma/period dropped, ? and ! kept.
const shown = (t: string) => t.replace(/[.,;:…]+$/u, '');

export const chunkWords = (words: Word[]): Chunk[] => {
  const groups: Word[][] = [];
  let cur: Word[] = [];
  let chars = 0;
  words.forEach((w, i) => {
    const prev = words[i - 1];
    const len = shown(w.text).length;
    const hard = Boolean(prev && (w.start - prev.end > MAX_GAP || SENT_END.test(prev.text)));
    const full = cur.length >= MAX_WORDS || chars + len > MAX_CHARS;
    if (cur.length && (hard || full)) {
      // Keep consecutive key words together: when a group is cut only because it is full,
      // trailing key words move to the next group with w (if they fit there).
      let k = cur.length;
      if (!hard && w.key) while (k > 1 && cur[k - 1].key) k--;
      const moved = cur.slice(k);
      const movedChars = moved.reduce((a, x) => a + shown(x.text).length, 0);
      if (moved.length && moved.length + 1 <= MAX_WORDS && movedChars + len <= MAX_CHARS) {
        groups.push(cur.slice(0, k));
        cur = moved;
        chars = movedChars;
      } else {
        groups.push(cur);
        cur = [];
        chars = 0;
      }
    }
    cur.push(w);
    chars += len;
  });
  if (cur.length) groups.push(cur);
  return groups.map((g, i) => {
    const next = groups[i + 1];
    const last = g[g.length - 1];
    // Short gaps: hold the group until the next one (no flicker); longer gaps: linger, then clear.
    const end = next ? (next[0].start - last.end <= MAX_GAP ? next[0].start : Math.min(last.end + LINGER, next[0].start)) : last.end + LINGER;
    return {index: i, words: g, start: g[0].start, end};
  });
};

const ChunkView: React.FC<{chunk: Chunk; theme: Theme; font: string}> = ({chunk, theme, font}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame - Math.round(chunk.start * fps);
  const enter = interpolate(t, [0, 4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const chars = chunk.words.reduce((a, w) => a + shown(w.text).length + 1, -1);
  const size = Math.floor(Math.min(FONT_MAX, (SAFE_W - 40) / (Math.max(chars, 6) * CHAR_EM)));
  return (
    <div
      style={{
        position: 'absolute',
        left: SAFE_X0,
        width: SAFE_W,
        top: CAPTION_Y,
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        columnGap: '0.28em',
        fontFamily: font,
        fontWeight: 800,
        fontSize: size,
        lineHeight: 1.15,
        textAlign: 'center',
        opacity: enter,
        transform: `translateY(-50%) translateY(${(1 - enter) * 10}px)`,
      }}
    >
      {chunk.words.map((w, i) => (
        <span
          key={i}
          style={{
            color: w.key ? theme.accent : theme.text,
            WebkitTextStroke: `8px ${alpha(theme.dark, 0.85)}`,
            paintOrder: 'stroke fill',
            textShadow: `0 4px 18px ${alpha(theme.dark, 0.55)}`,
          }}
        >
          {shown(w.text)}
        </span>
      ))}
    </div>
  );
};

export const Captions: React.FC<{words: Word[]; off: Span[]; theme: Theme; font: string}> = ({words, off, theme, font}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const chunks = useMemo(() => chunkWords(words), [words]);
  if (inAny(frame, off)) return null;
  const chunk = chunks.find((c) => frame >= Math.round(c.start * fps) && frame < Math.round(c.end * fps));
  return <AbsoluteFill>{chunk ? <ChunkView key={chunk.index} chunk={chunk} theme={theme} font={font} /> : null}</AbsoluteFill>;
};
