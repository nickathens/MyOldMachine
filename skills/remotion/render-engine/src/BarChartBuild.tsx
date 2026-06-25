import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const FONT = "Montserrat";

export const barChartDefaults = {
  title: "Render time by method",
  unit: "s",
  accent: "#C9A84C",
  background: "#0A0A0A",
  ink: "#F5F2EA",
  data: [
    { label: "Manual", value: 240 },
    { label: "Template", value: 90 },
    { label: "Automated", value: 18 },
  ],
};

type Props = typeof barChartDefaults;

// Animated bar chart: title rises in, bars spring up from the baseline with a
// stagger, value labels count up as each bar grows. Good for deck data beats.
export const BarChartBuild: React.FC<Props> = ({
  title,
  unit,
  accent,
  background,
  ink,
  data,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  const max = Math.max(...data.map((d) => d.value), 1);
  const chartW = width * 0.8;
  const chartH = height * 0.5;
  const left = (width - chartW) / 2;
  const baseY = height * 0.78;
  const slot = chartW / data.length;
  const barW = Math.min(slot * 0.5, 220);

  const titleS = spring({ frame, fps, config: { damping: 200 } });
  const titleO = interpolate(titleS, [0, 1], [0, 1]);
  const titleY = interpolate(titleS, [0, 1], [24, 0]);

  return (
    <AbsoluteFill style={{ backgroundColor: background, fontFamily: FONT }}>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 50% 38%, rgba(255,255,255,0.04), rgba(0,0,0,0) 62%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left,
          top: height * 0.12,
          color: ink,
          fontWeight: 700,
          fontSize: 56,
          letterSpacing: "-0.01em",
          opacity: titleO,
          transform: `translateY(${titleY}px)`,
        }}
      >
        {title}
      </div>
      <div
        style={{
          position: "absolute",
          left,
          top: baseY,
          width: chartW,
          height: 2,
          backgroundColor: "rgba(245,242,234,0.18)",
        }}
      />
      {data.map((d, i) => {
        const delay = 15 + i * 10;
        const s = spring({
          frame: frame - delay,
          fps,
          config: { damping: 200, mass: 0.8 },
        });
        const h = interpolate(s, [0, 1], [0, (d.value / max) * chartH]);
        const val = Math.round(
          interpolate(s, [0, 1], [0, d.value], { extrapolateRight: "clamp" }),
        );
        const cx = left + slot * i + slot / 2;
        const labelO = interpolate(s, [0, 0.4], [0, 1], {
          extrapolateRight: "clamp",
        });
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: "absolute",
                left: cx - barW / 2,
                top: baseY - h,
                width: barW,
                height: h,
                background: `linear-gradient(180deg, ${accent}, ${accent}AA)`,
                borderRadius: "6px 6px 0 0",
              }}
            />
            <div
              style={{
                position: "absolute",
                left: cx - slot / 2,
                top: baseY - h - 60,
                width: slot,
                textAlign: "center",
                color: ink,
                fontWeight: 700,
                fontSize: 42,
                opacity: labelO,
              }}
            >
              {val}
              {unit}
            </div>
            <div
              style={{
                position: "absolute",
                left: cx - slot / 2,
                top: baseY + 22,
                width: slot,
                textAlign: "center",
                color: "rgba(245,242,234,0.7)",
                fontWeight: 500,
                fontSize: 30,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
              }}
            >
              {d.label}
            </div>
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};
