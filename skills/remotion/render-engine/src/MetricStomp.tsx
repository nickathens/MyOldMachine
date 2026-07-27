import React from "react";
import {
  AbsoluteFill,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT } from "./font";
import { DigitRoll } from "./DigitRoll";
import { FlashCut } from "./FlashCut";
import { Caption } from "./Caption";
import { dampedSettle } from "./helpers/motion";

// Recipe: references/shots/data/odometer-digit-roll.md, with the lock-frame
// background flash borrowed from references/shots/typography/cel-flash-stomp.md.
//
// A single headline number owns the frame. Each digit rolls independently like
// a slot reel, left to right; the instant the last one locks, a warm bloom
// fires and the whole group takes one damped recoil. The number never moves
// after the lock, so the punch reads as weight rather than wobble.

export const metricStompDefaults = {
  value: "3200",
  label: "RENDERS SHIPPED",
  unit: "",
  caption: "STUDIO / 2026",
  accent: "#C9A84C",
  background: "#0A0A0A",
  ink: "#F5F2EA",
  digitSize: 300,
};

type Props = typeof metricStompDefaults;

// DigitRoll starts glyph i at `delay + i * 4` and takes 22 frames to land, so
// the last character locks here. Keep in sync with DigitRoll's own timing.
const ROLL_START = 12;
const lockFrame = (value: string) =>
  ROLL_START + Math.max(0, value.length - 1) * 4 + 22;

export const MetricStomp: React.FC<Props> = ({
  value,
  label,
  unit,
  caption,
  accent,
  background,
  ink,
  digitSize,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const text = String(value);
  const lock = lockFrame(text);

  const labelIn = spring({ frame: frame - 2, fps, config: { damping: 200 } });

  // One closed-form recoil at the lock, not an accumulating spring: the shot
  // must be identical on every render and on any frame range.
  const recoil = dampedSettle(frame - lock, 0.11, 0.22);
  const groupScale = 1 + recoil * 0.035;

  // The background deepens on the lock and stays deepened. Nothing in this
  // layer moves, so the punch lands in peripheral vision (cel-flash-stomp).
  const deepen = interpolate(frame, [lock, lock + 6], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const ruleScale = interpolate(frame, [lock + 2, lock + 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: background,
        fontFamily: FONT,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at 50% 46%, rgba(255,255,255,${
            0.06 - deepen * 0.03
          }), rgba(0,0,0,0) 58%), radial-gradient(circle at 50% 58%, rgba(0,0,0,0) 30%, rgba(0,0,0,${
            0.55 + deepen * 0.25
          }) 100%)`,
        }}
      />

      <div style={{ transform: `scale(${groupScale})`, textAlign: "center" }}>
        <div
          style={{
            color: accent,
            fontWeight: 600,
            fontSize: 30,
            letterSpacing: "0.42em",
            textTransform: "uppercase",
            paddingLeft: "0.42em",
            opacity: labelIn,
            transform: `translateY(${interpolate(labelIn, [0, 1], [16, 0])}px)`,
          }}
        >
          {label}
        </div>

        <div
          style={{
            marginTop: 26,
            display: "flex",
            alignItems: "baseline",
            justifyContent: "center",
            gap: 10,
            fontWeight: 800,
          }}
        >
          <DigitRoll
            value={text}
            delay={ROLL_START}
            fontSize={digitSize}
            color={ink}
          />
          {unit ? (
            <span
              style={{
                fontSize: digitSize * 0.32,
                color: accent,
                fontWeight: 700,
                opacity: interpolate(frame, [lock, lock + 8], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                }),
              }}
            >
              {unit}
            </span>
          ) : null}
        </div>

        <div
          style={{
            height: 3,
            width: 420,
            margin: "34px auto 0",
            backgroundColor: accent,
            transform: `scaleX(${ruleScale})`,
            transformOrigin: "center",
          }}
        />
      </div>

      {caption ? <Caption text={caption} duration={durationInFrames} /> : null}

      {/* Bloom fires on the lock frame and is gone in a third of a second.
          FlashCut reads useCurrentFrame() and ramps from 0, so it MUST be
          wrapped in a Sequence: mounted bare it would see the global frame,
          land past its own out point, and render nothing. */}
      <Sequence from={lock} durationInFrames={12} layout="none">
        <AbsoluteFill style={{ opacity: 0.55 }}>
          <FlashCut duration={12} />
        </AbsoluteFill>
      </Sequence>
    </AbsoluteFill>
  );
};
