import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const FONT = "Montserrat";

export const titleCardDefaults = {
  title: "REMOTION",
  subtitle: "MOTION GRAPHICS",
  accent: "#C9A84C",
  background: "#0A0A0A",
  ink: "#F5F2EA",
};

type Props = typeof titleCardDefaults;

// Cinematic title: words spring up and fade in with a stagger, a gold rule
// draws under them, the subtitle rises in after, and the whole group settles.
export const TitleCard: React.FC<Props> = ({
  title,
  subtitle,
  accent,
  background,
  ink,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const words = String(title).split(" ");

  const subOpacity = interpolate(frame, [40, 70], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subY = interpolate(frame, [40, 70], [22, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const ruleProgress = spring({
    frame: frame - 30,
    fps,
    config: { damping: 200 },
  });

  const settle = spring({ frame, fps, config: { damping: 200, mass: 1.2 } });
  const groupScale = interpolate(settle, [0, 1], [1.04, 1]);

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
          background:
            "radial-gradient(circle at 50% 44%, rgba(255,255,255,0.05), rgba(0,0,0,0) 55%), radial-gradient(circle at 50% 60%, rgba(0,0,0,0) 38%, rgba(0,0,0,0.62) 100%)",
        }}
      />
      <div style={{ transform: `scale(${groupScale})`, textAlign: "center" }}>
        <div
          style={{ display: "flex", gap: "0.25em", justifyContent: "center" }}
        >
          {words.map((w, i) => {
            const s = spring({
              frame: frame - i * 6,
              fps,
              config: { damping: 200 },
            });
            const y = interpolate(s, [0, 1], [90, 0]);
            const o = interpolate(s, [0, 1], [0, 1]);
            return (
              <span
                key={i}
                style={{
                  display: "inline-block",
                  transform: `translateY(${y}px)`,
                  opacity: o,
                  color: ink,
                  fontWeight: 900,
                  fontSize: 168,
                  letterSpacing: "-0.02em",
                  lineHeight: 1,
                }}
              >
                {w}
              </span>
            );
          })}
        </div>
        <div
          style={{
            height: 3,
            width: 360,
            margin: "38px auto 0",
            backgroundColor: accent,
            transform: `scaleX(${ruleProgress})`,
            transformOrigin: "center",
          }}
        />
        <div
          style={{
            marginTop: 30,
            color: accent,
            fontWeight: 500,
            fontSize: 34,
            letterSpacing: "0.45em",
            textTransform: "uppercase",
            opacity: subOpacity,
            transform: `translateY(${subY}px)`,
            paddingLeft: "0.45em",
          }}
        >
          {subtitle}
        </div>
      </div>
    </AbsoluteFill>
  );
};
