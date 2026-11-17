import React from 'react';
import {Composition} from 'remotion';
import Current, {
  durationInSeconds,
  fps,
  width,
  height,
} from './compositions/generated/Current';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Generated"
      component={Current}
      durationInFrames={Math.max(1, Math.round(durationInSeconds * fps))}
      fps={fps}
      width={width}
      height={height}
    />
  );
};
