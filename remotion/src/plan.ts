import type {OverlayOf, Shot, VideoProps, Word} from './schema';

// Props -> frame plan. Pure function: converts seconds to frames and resolves layout conflicts.
//  - no gaps between shots (no black frames): each shot runs until the next one starts
//  - the source tag waits until the hook is gone (both live in the top area)
//  - full cards (stat, quote) never stack: a later card cuts the earlier one short
//  - nothing but the outro after outro.at

export const FPS = 30;
export const W = 1080;
export const H = 1920;

export const toF = (s: number) => Math.round(s * FPS);

export type Span = {from: number; dur: number};
export const end = (s: Span) => s.from + s.dur;
export const inSpan = (f: number, s: Span) => f >= s.from && f < end(s);
export const inAny = (f: number, spans: Span[]) => spans.some((s) => inSpan(f, s));

/** overlays shorter than this (frames) are dropped */
const MIN_OVERLAY = 12;
/** warn when the outro gets less than this (frames) */
const OUTRO_MIN = 45;

export type PlannedShot = Shot & {
  span: Span;
  /** frames the source actually covers; the rest is a freeze of its last frame */
  own: number;
};

export type PlannedOverlay =
  | {type: 'source'; span: Span; o: OverlayOf<'source'>}
  | {type: 'stat'; span: Span; o: OverlayOf<'stat'>}
  | {type: 'quote'; span: Span; o: OverlayOf<'quote'>};

export type Plan = {
  total: number;
  shots: PlannedShot[];
  hook: Span;
  overlays: PlannedOverlay[];
  captionWords: Word[];
  captionsOff: Span[];
  outro: Span;
  warnings: string[];
};

/** Later item cuts the earlier one short; too-short leftovers are dropped. */
const resolveLane = <T extends {span: Span}>(items: T[]): T[] => {
  const sorted = [...items].sort((a, b) => a.span.from - b.span.from);
  return sorted
    .map((it, i) => {
      const next = sorted[i + 1];
      return next && next.span.from < end(it.span) ? {...it, span: {from: it.span.from, dur: next.span.from - it.span.from}} : it;
    })
    .filter((it) => it.span.dur >= MIN_OVERLAY);
};

export const planVideo = (p: VideoProps): Plan => {
  const warnings: string[] = [];
  const total = Math.max(1, Math.ceil(p.durationSec * FPS));
  const outroFrom = Math.min(Math.max(0, toF(p.outro.at)), total);
  const outro: Span = {from: outroFrom, dur: Math.max(1, total - outroFrom)};
  if (outro.dur < OUTRO_MIN) warnings.push(`outro is only ${(outro.dur / FPS).toFixed(2)} s`);

  // shots
  const src = p.shots.filter((s) => s.dur > 0).sort((a, b) => a.at - b.at);
  if (!src.length) warnings.push('no shots: only the background colour will show');
  const shots: PlannedShot[] = src
    .map((s, i) => {
      const from = i === 0 ? 0 : toF(s.at);
      const next = src[i + 1];
      const stop = next ? toF(next.at) : total;
      if (i === 0 && s.at > 0.05) warnings.push(`first shot starts at ${s.at}s: moved to 0`);
      if (next && toF(next.at) - toF(s.at + s.dur) > 2) warnings.push(`gap after shot ${i + 1}: filled by freezing its last frame`);
      return {...s, span: {from, dur: Math.max(1, stop - from)}, own: Math.max(1, toF(s.dur))};
    })
    .filter((s) => s.span.from < total);

  // hook ends at `until`, the outro or the first card, whichever comes first
  const cardStarts = p.overlays.filter((o) => o.type !== 'source').map((o) => toF(o.at)).filter((f) => f > 0);
  const hookEnd = Math.max(1, Math.min(toF(p.hook.until), outroFrom, ...cardStarts));
  const hook: Span = {from: 0, dur: hookEnd};

  // overlays
  const clip = (s: Span): Span => ({from: s.from, dur: Math.min(s.dur, outroFrom - s.from)});
  const afterHook = (s: Span): Span => (s.from < hookEnd ? {from: hookEnd, dur: end(s) - hookEnd} : s);
  const tags: PlannedOverlay[] = [];
  const cards: PlannedOverlay[] = [];
  for (const o of p.overlays) {
    const raw: Span = {from: toF(o.at), dur: toF(o.dur)};
    if (o.type === 'source') tags.push({type: 'source', o, span: clip(afterHook(raw))});
    else if (o.type === 'stat') {
      if (!o.items?.length) {
        warnings.push(`stat "${o.title}" has no items: skipped`);
        continue;
      }
      cards.push({type: 'stat', o, span: clip(raw)});
    } else cards.push({type: 'quote', o, span: clip(raw)});
  }
  const overlays = [...resolveLane(cards), ...resolveLane(tags)].filter((o) => o.span.from < total);

  // captions: off in captionsOff spans and during the outro
  const captionsOff: Span[] = (p.captionsOff ?? [])
    .map((c) => ({from: toF(c.from), dur: toF(c.to) - toF(c.from)}))
    .filter((s) => s.dur > 0);
  captionsOff.push(outro);
  const captionWords = [...p.words].sort((a, b) => a.start - b.start).filter((w) => !inAny(toF(w.start), captionsOff));

  return {total, shots, hook, overlays, captionWords, captionsOff, outro, warnings};
};
