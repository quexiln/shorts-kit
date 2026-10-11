import React from 'react';
import {AbsoluteFill, Easing, Sequence, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {SAFE, SAFE_W, SAFE_X0, alpha} from './theme';
import type {PlannedOverlay} from './plan';
import type {OverlayOf, Theme} from './schema';

// Source tag (top-left pill) and full cards (stat, quote) centred above the caption band.

type Look = {theme: Theme; font: string};

/** Cards sit between the hook/source area and the caption band (y ~1240). */
const CARD_CENTER_Y = 760;
const CARD_MAX_H = 700;

/** Fade in / out over a few frames; `scale` gives cards a slight settle. */
const useEnterExit = (inF = 6, outF = 6) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const enter = interpolate(frame, [0, inF], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const exit = interpolate(frame, [durationInFrames - outF, durationInFrames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return {opacity: Math.min(enter, exit), scale: 0.96 + 0.04 * enter, frame};
};

export const SourceTag: React.FC<Look & {o: OverlayOf<'source'>}> = ({o, theme, font}) => {
  const {opacity} = useEnterExit();
  return (
    <AbsoluteFill style={{opacity}}>
      <div
        style={{
          position: 'absolute',
          top: SAFE.top,
          left: SAFE_X0,
          maxWidth: SAFE_W,
          padding: '10px 20px',
          borderRadius: 999,
          background: alpha(theme.dark, 0.72),
          color: theme.text,
          fontFamily: font,
          fontSize: 28,
          fontWeight: 600,
          lineHeight: 1.2,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {o.source}
        {o.date ? <span style={{fontWeight: 400, opacity: 0.75}}>{`  ·  ${o.date}`}</span> : null}
      </div>
    </AbsoluteFill>
  );
};

const Panel: React.FC<Look & {children: React.ReactNode}> = ({theme, font, children}) => {
  const {opacity, scale} = useEnterExit(8, 6);
  return (
    <AbsoluteFill style={{opacity}}>
      <div
        style={{
          position: 'absolute',
          left: SAFE_X0,
          width: SAFE_W,
          top: CARD_CENTER_Y,
          maxHeight: CARD_MAX_H,
          overflow: 'hidden',
          boxSizing: 'border-box',
          padding: '44px 48px',
          borderRadius: 32,
          background: alpha(theme.dark, 0.88),
          color: theme.text,
          fontFamily: font,
          transform: `translateY(-50%) scale(${scale})`,
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
};

const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

export const StatCard: React.FC<Look & {o: OverlayOf<'stat'>}> = ({o, theme, font}) => {
  const frame = useCurrentFrame();
  const max = Math.max(...o.items.map((it) => Math.abs(it.value)), 1e-9);
  const rows = o.items.slice(0, 6);
  const grow = (i: number) => interpolate(frame, [6 + i * 3, 24 + i * 3], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <Panel theme={theme} font={font}>
      <div style={{fontSize: 40, fontWeight: 700, lineHeight: 1.2, marginBottom: 36}}>{o.title}</div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 30}}>
        {rows.map((it, i) => {
          const color = it.highlight ? theme.accent : theme.text;
          return (
            <div key={i}>
              <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', fontSize: 32, marginBottom: 12}}>
                <span style={{fontWeight: it.highlight ? 700 : 400, opacity: it.highlight ? 1 : 0.8}}>{it.label}</span>
                <span style={{fontWeight: 700, color}}>{`${fmt(it.value)}${o.unit ?? ''}`}</span>
              </div>
              <div style={{height: 18, borderRadius: 9, background: alpha(theme.text, 0.12)}}>
                <div
                  style={{
                    height: '100%',
                    borderRadius: 9,
                    width: `${(Math.abs(it.value) / max) * 100 * grow(i)}%`,
                    background: it.highlight ? theme.accent : alpha(theme.text, 0.55),
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
};

/** Quote text with the first case-insensitive match of `highlight` in the accent colour. */
const QuoteText: React.FC<{quote: string; highlight?: string; accent: string}> = ({quote, highlight, accent}) => {
  const at = highlight ? quote.toLowerCase().indexOf(highlight.toLowerCase()) : -1;
  if (!highlight || at < 0) return <>{quote}</>;
  return (
    <>
      {quote.slice(0, at)}
      <span style={{color: accent}}>{quote.slice(at, at + highlight.length)}</span>
      {quote.slice(at + highlight.length)}
    </>
  );
};

export const QuoteCard: React.FC<Look & {o: OverlayOf<'quote'>}> = ({o, theme, font}) => {
  const size = o.quote.length > 160 ? 40 : o.quote.length > 90 ? 48 : 56;
  return (
    <Panel theme={theme} font={font}>
      <div style={{fontSize: 120, fontWeight: 800, lineHeight: 0.6, height: 56, color: theme.accent}}>“</div>
      <div style={{fontSize: size, fontWeight: 700, lineHeight: 1.22, letterSpacing: -0.3}}>
        <QuoteText quote={o.quote} highlight={o.highlight} accent={theme.accent} />
      </div>
      <div style={{marginTop: 32, height: 2, width: 64, background: alpha(theme.text, 0.35)}} />
      <div style={{marginTop: 22, fontSize: 32, fontWeight: 700}}>{o.name}</div>
      {o.role ? <div style={{marginTop: 4, fontSize: 28, opacity: 0.75}}>{o.role}</div> : null}
    </Panel>
  );
};

const OverlayView: React.FC<Look & {p: PlannedOverlay}> = ({p, ...look}) => {
  if (p.type === 'source') return <SourceTag o={p.o} {...look} />;
  if (p.type === 'stat') return <StatCard o={p.o} {...look} />;
  return <QuoteCard o={p.o} {...look} />;
};

export const OverlaysLayer: React.FC<Look & {items: PlannedOverlay[]}> = ({items, ...look}) => (
  <>
    {items.map((p, i) => (
      <Sequence key={i} from={p.span.from} durationInFrames={p.span.dur} name={p.type}>
        <OverlayView p={p} {...look} />
      </Sequence>
    ))}
  </>
);
