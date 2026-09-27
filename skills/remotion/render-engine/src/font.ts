import { loadFont } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadGreekCompanion } from "@remotion/google-fonts/Manrope";

// Load Montserrat into the render bundle so output is the intended geometric
// sans on ANY machine, not Chromium's serif fallback. Without this, a box that
// lacks Montserrat system-wide silently substitutes a serif and every glyph
// changes. loadFont() registers the @font-face and blocks the render (via
// Remotion's delayRender) until the font is ready, so results are deterministic.
//
// Coverage note: Montserrat ships Latin, Latin-ext, Cyrillic and Vietnamese
// and has NO Greek subset. Greek used to fall through to whatever sans the
// machine had (DejaVu here), so a Greek title rendered in a different face on
// every box. Manrope, loaded for its Greek subset only, is the next family in
// the stack: a geometric sans close to Montserrat, the same companion the
// presentations skill uses for Greek. Chromium picks per character, so Latin
// stays Montserrat. Manrope stops at weight 800; a 900 Greek glyph renders at
// 800.
const { fontFamily } = loadFont("normal", {
  weights: ["400", "500", "700", "800", "900"],
  subsets: ["latin", "latin-ext"],
});
const { fontFamily: greekFamily } = loadGreekCompanion("normal", {
  weights: ["400", "500", "700", "800"],
  subsets: ["greek"],
});

export const FONT = `${fontFamily}, ${greekFamily}, sans-serif`;
