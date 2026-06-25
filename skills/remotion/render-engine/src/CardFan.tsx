import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT } from "./font";

// One reward card. Colours and copy are data so the whole fan is re-themeable
// from props without touching the animation.
type Card = {
  grad: [string, string];
  ink: string;
  big?: string;
  bigColor?: string;
  title: string[];
  sub: string;
  terms?: boolean;
  // A pill button is drawn only when `button` is set. The MYSTERY REWARD cards
  // carry no button.
  button?: string;
  pill?: string;
  pillInk?: string;
  icon?: "gift";
  giftBox?: string;
  giftRibbon?: string;
  // small scattered confetti dots inside the card
  confetti?: string;
  // faint floating mini-icons behind the content (the mystery cards)
  pattern?: boolean;
};

export type CardFanProps = {
  titleA: string;
  titleB: string;
  titleAColor: string;
  titleBColor: string;
  background: [string, string];
  cards: Card[];
  // When true the fan is built in real CSS 3D space: perspective, a tilted
  // viewing angle, per-card Z thickness and an outward bow. When false it is the
  // flat 2D fan (in-plane rotation only).
  dimensional?: boolean;
};

// Neutral loyalty / rewards demo content. Swap `cards` via --props to theme it
// for any brand or use case.
export const cardFanDefaults: CardFanProps = {
  titleA: "My",
  titleB: "Rewards",
  titleAColor: "#F4F7F8",
  titleBColor: "#1ec8d0",
  background: ["#0f3438", "#06090c"],
  cards: [
    {
      // teal — bonus points. The big number is white, the headings dark navy.
      grad: ["#1AE3CD", "#10B6A4"],
      ink: "#0A2E3A",
      big: "500",
      bigColor: "#FFFFFF",
      title: ["BONUS POINTS"],
      sub: "Thanks for being a member.",
      terms: true,
      button: "REDEEM",
      pill: "#141646",
      pillInk: "#ffffff",
      confetti: "#E7B53A",
    },
    {
      // lime — discount on next order
      grad: ["#CBF53A", "#A6D616"],
      ink: "#16203A",
      big: "15%",
      title: ["OFF NEXT ORDER"],
      sub: "A little gift for you.",
      terms: true,
      button: "REDEEM",
      pill: "#141646",
      pillInk: "#ffffff",
      confetti: "#E7B53A",
    },
    {
      // purple — mystery reward: teal gift box, gold ribbon, no button
      grad: ["#8A24A8", "#2C1BA6"],
      ink: "#ffffff",
      icon: "gift",
      giftBox: "#5FE6DC",
      giftRibbon: "#F4A52E",
      title: ["MYSTERY", "REWARD"],
      sub: "Tap to reveal your perk.",
      terms: true,
      pattern: true,
    },
    {
      // teal — double points (same turquoise family as card 1)
      grad: ["#1AE6D2", "#10AEB8"],
      ink: "#0A2E3A",
      big: "2x",
      title: ["POINTS WEEKEND"],
      sub: "Earn double all weekend.",
      terms: true,
      button: "REDEEM",
      pill: "#141646",
      pillInk: "#ffffff",
      confetti: "#E7B53A",
    },
    {
      // orange — mystery reward: purple gift box, pink ribbon, no button
      grad: ["#F7972F", "#EE6A28"],
      ink: "#ffffff",
      icon: "gift",
      giftBox: "#7A3BD0",
      giftRibbon: "#F25CA8",
      title: ["MYSTERY", "REWARD"],
      sub: "A little something extra.",
      terms: true,
      pattern: true,
    },
  ],
};

// Same content, rendered in real 3D space.
export const cardFan3DDefaults: CardFanProps = {
  ...cardFanDefaults,
  dimensional: true,
};

const CARD_W = 300;
const CARD_H = 400;
const PIVOT_X = 960;
const BASE_TOP = 372; // card top position when upright (closed)
const PIVOT_DIST = 980; // px below each card's top where the fan hinges
const PIVOT_Y = BASE_TOP + PIVOT_DIST; // hinge point in screen space
const STEP = 15; // in-plane splay between adjacent cards when fully open (wide arc, all cards read)
const MIN_STEP = 4.2; // residual splay when closed: a readable stepped deck, never "one inside the other"
const CURL = 13; // rotateY per card-step: the open fan curls into a 3D cone toward the camera
const BOW = 14; // symmetric Z bow (px per step) giving the deck real thickness even when closed

type Bubble = {
  x: number;
  y: number;
  r: number;
  c: string;
  blur: number;
  amp: number;
  ph: number;
  fg?: boolean;
  op?: number;
};

const T = "#1ec8d0";
const L = "#b6e23a";
const A = "#f4c430";

const BUBBLES: Bubble[] = [
  { x: 120, y: 190, r: 46, c: T, blur: 0, amp: 14, ph: 0 },
  { x: 250, y: 520, r: 30, c: L, blur: 0, amp: 10, ph: 1 },
  { x: 95, y: 770, r: 60, c: T, blur: 3, amp: 16, ph: 2 },
  { x: 310, y: 885, r: 26, c: A, blur: 0, amp: 9, ph: 0.5 },
  { x: 1755, y: 165, r: 40, c: L, blur: 0, amp: 12, ph: 1.5 },
  { x: 1825, y: 520, r: 54, c: T, blur: 2, amp: 15, ph: 2.2 },
  { x: 1685, y: 825, r: 34, c: A, blur: 0, amp: 10, ph: 0.8 },
  { x: 1505, y: 125, r: 24, c: T, blur: 0, amp: 8, ph: 1.1 },
  { x: 560, y: 135, r: 20, c: A, blur: 0, amp: 7, ph: 3 },
  { x: 985, y: 95, r: 30, c: L, blur: 0, amp: 9, ph: 2.5 },
  { x: 1305, y: 935, r: 42, c: T, blur: 1, amp: 12, ph: 0.3 },
  { x: 705, y: 955, r: 28, c: L, blur: 0, amp: 9, ph: 1.7 },
  { x: 385, y: 305, r: 90, c: T, blur: 10, amp: 18, ph: 0.6, fg: true, op: 0.5 },
  { x: 1625, y: 380, r: 80, c: L, blur: 12, amp: 18, ph: 2.0, fg: true, op: 0.45 },
  { x: 1180, y: 1000, r: 70, c: A, blur: 10, amp: 14, ph: 1.2, fg: true, op: 0.5 },
];

const Sphere: React.FC<{ b: Bubble; frame: number }> = ({ b, frame }) => {
  const dy = Math.sin(frame / 42 + b.ph) * b.amp;
  const dx = Math.cos(frame / 55 + b.ph) * (b.amp * 0.4);
  return (
    <div
      style={{
        position: "absolute",
        left: b.x - b.r,
        top: b.y - b.r,
        width: b.r * 2,
        height: b.r * 2,
        borderRadius: "50%",
        transform: `translate(${dx}px, ${dy}px)`,
        background: `radial-gradient(circle at 34% 30%, rgba(255,255,255,0.95) 0%, ${b.c} 40%, rgba(0,0,0,0.38) 108%)`,
        boxShadow: `0 0 ${b.r * 0.9}px ${b.c}66, inset -6px -8px 14px rgba(0,0,0,0.25)`,
        opacity: b.op ?? 0.92,
        filter: b.blur ? `blur(${b.blur}px)` : undefined,
      }}
    />
  );
};

// A colourful wrapped gift: coloured box + contrasting ribbon, lid highlight and
// base shadow for depth, and a few sparkle stars (teal box / gold ribbon on the
// purple card, purple box / pink ribbon on the orange one).
const Gift: React.FC<{ size: number; box: string; ribbon: string }> = ({
  size,
  box,
  ribbon,
}) => {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      style={{ filter: "drop-shadow(0 6px 8px rgba(0,0,0,0.35))" }}
    >
      {/* sparkle stars around the gift */}
      <path
        d="M7 12 l1.6 3.4 3.4 1.6 -3.4 1.6 -1.6 3.4 -1.6 -3.4 -3.4 -1.6 3.4 -1.6 z"
        fill="#FFD23F"
      />
      <path
        d="M56 17 l1.2 2.5 2.5 1.2 -2.5 1.2 -1.2 2.5 -1.2 -2.5 -2.5 -1.2 2.5 -1.2 z"
        fill="#FF6FB0"
      />
      <circle cx="53" cy="41" r="1.8" fill="#FFD23F" />
      {/* bows */}
      <ellipse cx="25" cy="14.5" rx="9" ry="6.5" fill={ribbon} />
      <ellipse cx="39" cy="14.5" rx="9" ry="6.5" fill={ribbon} />
      {/* box body + base shadow */}
      <rect x="9" y="31" width="46" height="27" rx="3.5" fill={box} />
      <rect x="9" y="49" width="46" height="9" rx="3.5" fill="rgba(0,0,0,0.18)" />
      {/* lid + top highlight */}
      <rect x="6" y="20" width="52" height="12.5" rx="3.5" fill={box} />
      <rect x="6" y="20" width="52" height="5" rx="3.5" fill="rgba(255,255,255,0.30)" />
      {/* vertical ribbon + knot */}
      <rect x="27.5" y="20" width="9" height="38" fill={ribbon} />
      <circle cx="32" cy="15.5" r="3.6" fill={ribbon} />
    </svg>
  );
};

// Fixed scatter positions (percent of card) for the small confetti dots.
const CONFETTI: { l: number; t: number; r: number }[] = [
  { l: 13, t: 26, r: 5 },
  { l: 86, t: 20, r: 4 },
  { l: 80, t: 60, r: 6 },
  { l: 18, t: 66, r: 4 },
  { l: 50, t: 12, r: 4 },
  { l: 10, t: 46, r: 5 },
];

// Faint floating mini-icons behind the MYSTERY REWARD content.
const PATTERN: { l: number; t: number; s: number }[] = [
  { l: 16, t: 16, s: 11 },
  { l: 82, t: 14, s: 9 },
  { l: 12, t: 44, s: 8 },
  { l: 86, t: 50, s: 10 },
  { l: 20, t: 72, s: 9 },
  { l: 78, t: 76, s: 8 },
];

const RewardCard: React.FC<{ card: Card }> = ({ card }) => {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        borderRadius: 26,
        padding: 22,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        color: card.ink,
        background: `linear-gradient(160deg, ${card.grad[0]} 0%, ${card.grad[1]} 100%)`,
        border: "2px solid rgba(255,255,255,0.45)",
        boxShadow:
          "0 26px 50px rgba(0,0,0,0.45), inset 0 2px 10px rgba(255,255,255,0.4)",
        overflow: "hidden",
      }}
    >
      {/* gloss highlight */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "46%",
          background:
            "linear-gradient(180deg, rgba(255,255,255,0.35) 0%, rgba(255,255,255,0) 100%)",
          pointerEvents: "none",
        }}
      />
      {/* confetti dots */}
      {card.confetti &&
        CONFETTI.map((d, i) => (
          <div
            key={`cf${i}`}
            style={{
              position: "absolute",
              left: `${d.l}%`,
              top: `${d.t}%`,
              width: d.r * 2,
              height: d.r * 2,
              borderRadius: "50%",
              background: `radial-gradient(circle at 38% 32%, #fff4c2 0%, ${card.confetti} 58%, rgba(0,0,0,0.28) 130%)`,
              border: "1px solid rgba(0,0,0,0.12)",
              boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
              pointerEvents: "none",
            }}
          />
        ))}
      {/* faint floating mini gift icons on the mystery cards */}
      {card.pattern &&
        PATTERN.map((p, i) => (
          <div
            key={`pt${i}`}
            style={{
              position: "absolute",
              left: `${p.l}%`,
              top: `${p.t}%`,
              width: p.s,
              height: p.s,
              borderRadius: 2,
              border: "1.5px solid rgba(255,255,255,0.5)",
              transform: "rotate(45deg)",
              pointerEvents: "none",
            }}
          />
        ))}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 6,
          zIndex: 1,
        }}
      >
        {card.icon === "gift" ? (
          <Gift
            size={96}
            box={card.giftBox ?? "#ffffff"}
            ribbon={card.giftRibbon ?? "#ff5fa2"}
          />
        ) : (
          <div
            style={{
              fontWeight: 900,
              fontSize: card.big && card.big.length > 2 ? 70 : 94,
              lineHeight: 0.9,
              letterSpacing: "-0.03em",
              color: card.bigColor ?? card.ink,
              textShadow:
                card.bigColor === "#FFFFFF"
                  ? "0 3px 8px rgba(0,0,0,0.28)"
                  : undefined,
            }}
          >
            {card.big}
          </div>
        )}
        <div style={{ marginTop: 4 }}>
          {card.title.map((t, i) => (
            <div
              key={i}
              style={{
                fontWeight: 800,
                fontSize: 27,
                lineHeight: 1.04,
                letterSpacing: "-0.01em",
              }}
            >
              {t}
            </div>
          ))}
        </div>
        <div
          style={{
            marginTop: 6,
            fontWeight: 700,
            fontSize: 14,
            lineHeight: 1.2,
            opacity: 0.95,
            maxWidth: 210,
          }}
        >
          {card.sub}
        </div>
        {/* tiny terms microcopy, illegible by design, just texture */}
        {card.terms && (
          <div
            style={{
              marginTop: 5,
              fontWeight: 500,
              fontSize: 7,
              lineHeight: 1.35,
              opacity: 0.5,
              maxWidth: 188,
            }}
          >
            Terms and conditions apply. Offer valid for a limited time. Rewards
            have no cash value. See website for full details.
          </div>
        )}
      </div>
      {card.button && (
        <div
          style={{
            marginTop: 8,
            zIndex: 1,
            padding: "11px 26px",
            borderRadius: 999,
            background: card.pill,
            color: card.pillInk,
            fontWeight: 800,
            fontSize: 15,
            letterSpacing: "0.06em",
            boxShadow: "0 6px 14px rgba(0,0,0,0.3)",
          }}
        >
          {card.button}
        </div>
      )}
    </div>
  );
};

// Fan of reward cards that opens like a hand fan, holds the spread, then folds
// back to a stack. Each card pivots around a shared bottom point.
export const CardFan: React.FC<CardFanProps> = ({
  titleA,
  titleB,
  titleAColor,
  titleBColor,
  background,
  cards,
  dimensional = false,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const N = cards.length;
  const mid = (N - 1) / 2;

  const openStart = 12;
  const closeStart = 168;
  const stagger = 7;

  const titleSpring = spring({ frame: frame - 4, fps, config: { damping: 200 } });
  const titleY = interpolate(titleSpring, [0, 1], [-40, 0]);
  const titleO = titleSpring;
  const sway = Math.sin(frame / 40) * 0.5;

  // 3D viewing angle: look slightly down on the fan and let it turn left/right
  // so the depth and card thickness read clearly.
  const tiltX = 16 + Math.sin(frame / 65) * 1.5;
  const swayY = Math.sin(frame / 55) * 7;

  // Shared per-card opening maths, used by both the 2D and 3D branches.
  const cardState = (i: number) => {
    const openS = spring({
      frame: frame - openStart - i * stagger,
      fps,
      config: { damping: 12, mass: 0.85 },
    });
    const closeS = spring({
      frame: frame - closeStart - (N - 1 - i) * stagger,
      fps,
      config: { damping: 14 },
    });
    const openness = Math.max(0, Math.min(1, openS - closeS));
    return { openness, angle: (i - mid) * STEP * openness };
  };

  return (
    <AbsoluteFill
      style={{
        fontFamily: FONT,
        background: `radial-gradient(circle at 50% 38%, ${background[0]} 0%, ${background[1]} 78%)`,
      }}
    >
      {/* background bubbles */}
      {BUBBLES.filter((b) => !b.fg).map((b, i) => (
        <Sphere key={`bg${i}`} b={b} frame={frame} />
      ))}

      {/* frosted glass tray */}
      <div
        style={{
          position: "absolute",
          left: 235,
          top: 70,
          width: 1450,
          height: 900,
          borderRadius: 40,
          background: "rgba(255,255,255,0.07)",
          border: "1.5px solid rgba(255,255,255,0.22)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          boxShadow: "inset 0 1px 30px rgba(255,255,255,0.08)",
        }}
      />

      {/* title */}
      <div
        style={{
          position: "absolute",
          top: 150,
          width: "100%",
          textAlign: "center",
          fontWeight: 800,
          fontSize: 82,
          letterSpacing: "-0.01em",
          opacity: titleO,
          transform: `translateY(${titleY}px)`,
          textShadow: "0 6px 24px rgba(0,0,0,0.45)",
        }}
      >
        <span style={{ color: titleAColor }}>{titleA} </span>
        <span style={{ color: titleBColor }}>{titleB}</span>
      </div>

      {/* card fan */}
      {dimensional ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            perspective: 1600,
            perspectiveOrigin: "50% 42%",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              transformStyle: "preserve-3d",
              transform: `rotateX(${tiltX}deg) rotateY(${swayY}deg)`,
              transformOrigin: `${PIVOT_X}px 580px`,
            }}
          >
            {cards.map((card, i) => {
              const { openness } = cardState(i);
              const off = i - mid;
              // In-plane splay around the bottom rivet, with a small residual
              // spread when closed so the fold ends as a readable 3D deck and
              // never collapses into one card.
              const splay = off * (MIN_STEP + (STEP - MIN_STEP) * openness);
              // rotateY turns each blade in depth: the open fan curls into a
              // cone toward the camera (middle facing you, ends angling away).
              // Animating this with openness is what makes the fold read as 3D.
              const curl = off * CURL * openness;
              // Depth has two parts. A monotonic stack term (i * BOW) is always
              // present so the closed fan is a clean front-to-back deck where
              // every card's edge shows, never one card hiding the rest. A
              // symmetric convex bow is added only as it opens, curving the
              // spread fan toward the camera.
              const z = i * BOW + Math.abs(off) * 25 * openness;
              return (
                <div
                  key={i}
                  style={{
                    position: "absolute",
                    left: PIVOT_X - CARD_W / 2,
                    top: BASE_TOP,
                    width: CARD_W,
                    height: CARD_H,
                    transformOrigin: `50% ${PIVOT_DIST}px`,
                    transform: `rotate(${splay}deg) translateZ(${z}px)`,
                    transformStyle: "preserve-3d",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      transform: `rotateY(${curl}deg)`,
                      transformStyle: "preserve-3d",
                      backfaceVisibility: "hidden",
                    }}
                  >
                    <RewardCard card={card} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div
          style={{
            position: "absolute",
            inset: 0,
            transform: `rotate(${sway}deg)`,
            transformOrigin: `${PIVOT_X}px ${PIVOT_Y}px`,
          }}
        >
          {cards.map((card, i) => {
            const { angle } = cardState(i);
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: PIVOT_X - CARD_W / 2,
                  top: BASE_TOP,
                  width: CARD_W,
                  height: CARD_H,
                  transformOrigin: `50% ${PIVOT_DIST}px`,
                  transform: `rotate(${angle}deg)`,
                  zIndex: 100 - Math.abs(i - mid),
                }}
              >
                <RewardCard card={card} />
              </div>
            );
          })}
        </div>
      )}

      {/* contact shadow under the stack */}
      <div
        style={{
          position: "absolute",
          left: PIVOT_X - 230,
          top: BASE_TOP + CARD_H - 18,
          width: 460,
          height: 44,
          borderRadius: "50%",
          background: "rgba(0,0,0,0.4)",
          filter: "blur(20px)",
        }}
      />

      {/* foreground bubbles for depth */}
      {BUBBLES.filter((b) => b.fg).map((b, i) => (
        <Sphere key={`fg${i}`} b={b} frame={frame} />
      ))}
    </AbsoluteFill>
  );
};
