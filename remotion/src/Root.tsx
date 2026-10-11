import React from 'react';
import {Composition} from 'remotion';
import {Video, calculateMetadata} from './Video';
import {DEMO_PROPS} from './demo';
import {FPS, H, W} from './plan';

// One composition. Its real length comes from props.durationSec via calculateMetadata.
export const RemotionRoot: React.FC = () => (
  <Composition
    id="Video"
    component={Video}
    width={W}
    height={H}
    fps={FPS}
    durationInFrames={Math.ceil(DEMO_PROPS.durationSec * FPS)}
    defaultProps={DEMO_PROPS}
    calculateMetadata={calculateMetadata}
  />
);
