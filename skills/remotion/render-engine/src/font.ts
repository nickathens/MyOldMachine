import { loadFont } from "@remotion/google-fonts/Montserrat";

// Load Montserrat into the render bundle so output is the intended geometric
// sans on ANY machine, not Chromium's serif fallback. Without this, a box that
// lacks Montserrat system-wide silently substitutes a serif and every glyph
// changes. loadFont() registers the @font-face and blocks the render (via
// Remotion's delayRender) until the font is ready, so results are deterministic.
//
// Coverage note: Montserrat ships Latin, Latin-ext, Cyrillic and Vietnamese.
// It has NO Greek glyphs (only Λ and Δ exist), so Greek and other unsupported
// scripts fall back to the platform sans-serif named below. If a composition
// needs clean Greek, swap this module to a Greek-capable family, e.g.
// `@remotion/google-fonts/NotoSans`, and re-export FONT.
const { fontFamily } = loadFont("normal", {
  weights: ["400", "500", "700", "800", "900"],
  subsets: ["latin", "latin-ext"],
});

export const FONT = `${fontFamily}, sans-serif`;
