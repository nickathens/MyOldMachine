// Vendored from github.com/Vincentwei1021/video-shotcraft (Apache-2.0)
// at 93fe427, 2026-07-27. See ../../references/NOTICE.md.
// Local change: explicit React import (our tsconfig types components as React.FC
// and this engine ships no typescript compiler, so the namespace must be real).
import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';

/** Bright-field cut: a warm-white bloom that flashes over the hard cut. */
export const FlashCut: React.FC<{ duration?: number }> = ({ duration = 10 }) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, duration * 0.4, duration], [0, 0.85, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        opacity: o,
        background: 'radial-gradient(ellipse at 50% 45%, rgba(255,248,235,0.98), rgba(255,244,224,0.55) 55%, transparent 80%)',
      }}
    />
  );
};
