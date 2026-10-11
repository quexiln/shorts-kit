import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {SAFE, SAFE_W, SAFE_X0, alpha, parseEmphasis} from './theme';
import type {Theme} from './schema';

// Opening title. Fully visible on frame 0 (it doubles as the thumbnail), fades out at the end of its Sequence.

const FADE = 6;

export const Hook: React.FC<{title: string; theme: Theme; font: string}> = ({title, theme, font}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const opacity = interpolate(frame, [durationInFrames - FADE, durationInFrames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const chars = title.replace(/\*/g, '').split('\n').reduce((a, l) => Math.max(a, l.length), 0);
  // Long titles shrink; short ones stay at a comfortable headline size.
  const size = chars > 28 ? 64 : chars > 18 ? 72 : 80;
  return (
    <AbsoluteFill style={{opacity}}>
      <div style={{position: 'absolute', top: SAFE.top, left: SAFE_X0, width: SAFE_W, display: 'flex', justifyContent: 'center'}}>
        <div
          style={{
            maxWidth: SAFE_W,
            padding: '28px 36px 32px',
            borderRadius: 28,
            background: alpha(theme.dark, 0.72),
            fontFamily: font,
            fontWeight: 800,
            fontSize: size,
            lineHeight: 1.12,
            letterSpacing: -0.5,
            textAlign: 'center',
            color: theme.text,
            textWrap: 'balance',
          }}
        >
          {parseEmphasis(title).map((r, i) =>
            r.br ? <br key={i} /> : <span key={i} style={{color: r.em ? theme.accent : undefined}}>{r.text}</span>,
          )}
        </div>
      </div>
    </AbsoluteFill>
  );
};
