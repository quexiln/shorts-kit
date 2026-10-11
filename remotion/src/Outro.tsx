import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {SAFE_W, SAFE_X0, alpha, parseEmphasis} from './theme';
import type {Theme} from './schema';

// Closing line over a dark scrim, handle underneath. Holds until the end of the video.

export const Outro: React.FC<{text: string; theme: Theme; font: string}> = ({text, theme, font}) => {
  const frame = useCurrentFrame();
  const ease = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)} as const;
  const scrim = interpolate(frame, [0, 10], [0, 0.78], ease);
  const line = interpolate(frame, [4, 16], [0, 1], ease);
  const handle = interpolate(frame, [12, 24], [0, 1], ease);
  return (
    <AbsoluteFill style={{background: alpha(theme.dark, scrim)}}>
      <div
        style={{
          position: 'absolute',
          left: SAFE_X0,
          width: SAFE_W,
          top: 860,
          transform: 'translateY(-50%)',
          textAlign: 'center',
          fontFamily: font,
          color: theme.text,
        }}
      >
        <div style={{fontSize: 92, fontWeight: 800, lineHeight: 1.1, letterSpacing: -1, textWrap: 'balance', opacity: line, transform: `translateY(${(1 - line) * 16}px)`}}>
          {parseEmphasis(text).map((r, i) => (r.br ? <br key={i} /> : <span key={i} style={{color: r.em ? theme.accent : undefined}}>{r.text}</span>))}
        </div>
        {theme.handle ? <div style={{marginTop: 36, fontSize: 40, fontWeight: 600, color: theme.accent, opacity: handle}}>{theme.handle}</div> : null}
      </div>
    </AbsoluteFill>
  );
};
