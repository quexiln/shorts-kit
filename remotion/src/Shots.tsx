import React from 'react';
import {AbsoluteFill, Easing, Freeze, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {SAFE, alpha} from './theme';
import {toF, type PlannedShot} from './plan';
import type {Theme} from './schema';

// Cover crop to 9:16 around the focus point. Scale never drops below 1, so there is
// never a letterbox or blurred fill; zoom is capped well under the 1.12x framing limit.

const ZOOM_CAP = 1.1;
const PUNCH_PEAK = 1.08;
const SLOW_PEAK = 1.06;

/** Path relative to --public-dir -> URL; absolute http/data URLs pass through. */
export const resolveSrc = (s: string) =>
  /^(https?:|data:|blob:)/i.test(s) ? s : staticFile(s.replace(/\\/g, '/').replace(/^\.?\/+/, ''));

const clamp01 = (v: number | undefined) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0.5);

export const shotScale = (shot: PlannedShot, frame: number): number => {
  const zoom = shot.zoom ?? (shot.kind === 'image' ? 'slow' : 'none');
  let s = 1;
  if (zoom === 'punch') {
    s = interpolate(frame, [0, 5, 20], [1, PUNCH_PEAK, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.quad)});
  } else if (zoom === 'slow') {
    s = interpolate(frame, [0, Math.max(1, shot.span.dur - 1)], [1, SLOW_PEAK], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  }
  return Math.max(1, Math.min(ZOOM_CAP, s));
};

const ShotView: React.FC<{shot: PlannedShot; bg: string}> = ({shot, bg}) => {
  const frame = useCurrentFrame();
  const pos = `${(clamp01(shot.focusX) * 100).toFixed(2)}% ${(clamp01(shot.focusY) * 100).toFixed(2)}%`;
  const style: React.CSSProperties = {position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', objectPosition: pos};
  const media =
    shot.kind === 'video' ? (
      <OffthreadVideo src={resolveSrc(shot.src)} muted trimBefore={Math.max(0, toF(shot.from ?? 0))} style={style} />
    ) : (
      <Img src={resolveSrc(shot.src)} style={style} />
    );
  // Source ran out (gap fill): hold its last frame instead of showing black.
  const frozen = shot.kind === 'video' && frame >= shot.own;
  return (
    <AbsoluteFill style={{overflow: 'hidden', background: bg}}>
      <AbsoluteFill style={{transform: `scale(${shotScale(shot, frame)})`, transformOrigin: pos}}>
        {frozen ? <Freeze frame={shot.own - 1}>{media}</Freeze> : media}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** Illustrative label and credit, bottom-right just above the bottom UI zone. */
const FootageTag: React.FC<{shot: PlannedShot; theme: Theme; font: string}> = ({shot, theme, font}) => (
  <AbsoluteFill>
    <div
      style={{
        position: 'absolute',
        right: SAFE.right,
        bottom: SAFE.bottom + 16,
        maxWidth: 1080 - SAFE.left - SAFE.right,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '6px 14px',
        borderRadius: 999,
        background: alpha(theme.dark, 0.65),
        fontFamily: font,
        fontSize: 22,
        lineHeight: 1.2,
        color: theme.text,
      }}
    >
      {shot.credit ? (
        <span style={{minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', opacity: 0.85}}>{shot.credit}</span>
      ) : null}
      {shot.credit && shot.illustrative ? <span style={{opacity: 0.5}}>·</span> : null}
      {shot.illustrative ? <span style={{flexShrink: 0, fontWeight: 700, letterSpacing: 1.5}}>{theme.illustrativeLabel}</span> : null}
    </div>
  </AbsoluteFill>
);

export const ShotsLayer: React.FC<{shots: PlannedShot[]; theme: Theme; font: string}> = ({shots, theme, font}) => (
  <>
    {shots.map((s, i) => (
      <Sequence key={i} from={s.span.from} durationInFrames={s.span.dur} name={`shot ${i + 1}`}>
        <ShotView shot={s} bg={theme.dark} />
        {s.illustrative || s.credit ? <FootageTag shot={s} theme={theme} font={font} /> : null}
      </Sequence>
    ))}
  </>
);
