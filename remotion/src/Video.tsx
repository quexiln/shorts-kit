import React, {useMemo} from 'react';
import {AbsoluteFill, Sequence, type CalculateMetadataFunction} from 'remotion';
import {Captions} from './Captions';
import {Hook} from './Hook';
import {OverlaysLayer} from './Overlays';
import {Outro} from './Outro';
import {ShotsLayer} from './Shots';
import {SoundLayer} from './Sound';
import {DEMO_PROPS} from './demo';
import {FPS, planVideo} from './plan';
import type {VideoProps} from './schema';
import {fontFamily} from './theme';

// Layers, bottom to top: shots (+ footage tag), overlays, hook, captions, outro. Audio alongside.

export const Video: React.FC<VideoProps> = (props) => {
  const plan = useMemo(() => planVideo(props), [props]);
  const {theme} = props;
  const font = useMemo(() => fontFamily(theme.font), [theme.font]);
  return (
    <AbsoluteFill style={{background: theme.dark}}>
      <ShotsLayer shots={plan.shots} theme={theme} font={font} />
      <OverlaysLayer items={plan.overlays} theme={theme} font={font} />
      <Sequence from={plan.hook.from} durationInFrames={plan.hook.dur} name="hook">
        <Hook title={props.hook.title} theme={theme} font={font} />
      </Sequence>
      <Captions words={plan.captionWords} off={plan.captionsOff} theme={theme} font={font} />
      <Sequence from={plan.outro.from} durationInFrames={plan.outro.dur} name="outro">
        <Outro text={props.outro.text} theme={theme} font={font} />
      </Sequence>
      <SoundLayer p={props} />
    </AbsoluteFill>
  );
};

// Remotion merges --props shallowly over defaultProps, so an optional field missing from props.json
// would silently inherit the demo value (demo music, demo sfx). Drop optional fields that still equal
// the demo; a required field equal to the demo means props.json is incomplete.
const OPTIONAL: (keyof VideoProps)[] = ['music', 'sfx', 'captionsOff'];
// Only content fields: theme/hook/outro may legitimately equal the demo defaults (e.g. unmodified channel.example.json).
const REQUIRED: (keyof VideoProps)[] = ['voice', 'words', 'shots'];
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export const stripDemoLeak = (props: VideoProps): VideoProps => {
  if (props.voice?.src?.startsWith('demo/')) return props;
  const missing = REQUIRED.filter((k) => same(props[k], DEMO_PROPS[k]));
  if (missing.length) throw new Error(`Video: props.json is missing required fields: ${missing.join(', ')}`);
  const out = {...props};
  for (const k of OPTIONAL) if (k in out && same(out[k], DEMO_PROPS[k])) delete out[k];
  return out;
};

export const calculateMetadata: CalculateMetadataFunction<VideoProps> = ({props: input}) => {
  const props = stripDemoLeak(input);
  if (!(props.durationSec > 0)) throw new Error(`Video: invalid durationSec (${props.durationSec})`);
  if (!props.voice?.src) throw new Error('Video: voice.src is required');
  for (const w of planVideo(props).warnings) console.warn(`[Video] ${w}`);
  return {durationInFrames: Math.max(1, Math.ceil(props.durationSec * FPS)), fps: FPS, props};
};
