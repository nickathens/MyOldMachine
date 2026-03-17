# Impeccable — Frontend Design Skill

Production-grade frontend design knowledge. Use when building web components, pages, or applications. Generates creative, polished code that avoids generic AI aesthetics.

Based on [Impeccable](https://github.com/pbakaus/impeccable) (Apache 2.0, built on Anthropic's frontend-design skill).

**When to use:** Any time you're building or modifying HTML/CSS/JS -- websites, landing pages, web apps, email templates, UI components.

**Before using:** Read the relevant reference docs in `skills/impeccable/reference/` for the specific design domain you're working on.

---

## Reference Documents

Read these for deep guidance on specific design domains:

| Document | When to read |
|----------|-------------|
| `reference/typography.md` | Font selection, pairing, scales, fluid type, web font loading |
| `reference/color-and-contrast.md` | OKLCH, palettes, dark mode, tinted neutrals, WCAG contrast |
| `reference/spatial-design.md` | Spacing systems, grids, visual hierarchy, container queries |
| `reference/motion-design.md` | Timing, easing curves, staggering, reduced motion, perceived performance |
| `reference/interaction-design.md` | States, focus rings, forms, loading, keyboard navigation |
| `reference/responsive-design.md` | Mobile-first, breakpoints, input detection, safe areas, images |
| `reference/ux-writing.md` | Button labels, error messages, empty states, voice/tone, translation |

---

## Core Design Guidelines

### Design Direction

Commit to a BOLD aesthetic direction:
- **Purpose**: What problem does this interface solve? Who uses it?
- **Tone**: Pick an extreme: brutally minimal, maximalist, retro-futuristic, organic, luxury, playful, editorial, brutalist, art deco, soft/pastel, industrial. There are many flavors -- design one true to the aesthetic direction.
- **Differentiation**: What makes this UNFORGETTABLE?

Choose a clear conceptual direction and execute with precision. Bold maximalism and refined minimalism both work -- the key is intentionality, not intensity.

### Typography
- Choose distinctive fonts. Pair a display font with a refined body font.
- Use a modular type scale with fluid sizing (clamp).
- Vary weights and sizes for clear visual hierarchy.
- **DON'T**: Inter, Roboto, Arial, Open Sans, system defaults, Montserrat.
- **DON'T**: Monospace as lazy shorthand for "technical/developer" vibes.
- **DON'T**: Large rounded-corner icons above every heading -- they look templated.

### Color & Theme
- Use OKLCH for perceptually uniform, maintainable palettes.
- Tint neutrals toward your brand hue -- even subtle hints create cohesion.
- **DON'T**: Gray text on colored backgrounds -- use a shade of the background color.
- **DON'T**: Pure black (#000) or pure white (#fff) -- always tint.
- **DON'T**: Cyan-on-dark, purple-to-blue gradients, neon accents on dark backgrounds (the "AI color palette").
- **DON'T**: Gradient text for "impact" on metrics or headings.
- **DON'T**: Default to dark mode with glowing accents.

### Layout & Space
- Create visual rhythm through varied spacing -- tight groupings, generous separations.
- Use fluid spacing with clamp().
- Use asymmetry and break the grid intentionally for emphasis.
- **DON'T**: Wrap everything in cards. Don't nest cards inside cards.
- **DON'T**: Identical card grids -- same-sized cards with icon + heading + text, repeated endlessly.
- **DON'T**: Center everything -- left-aligned text with asymmetric layouts feels more designed.
- **DON'T**: Same spacing everywhere -- without rhythm, layouts feel monotonous.

### Visual Details
- Use intentional, purposeful decorative elements that reinforce brand.
- **DON'T**: Glassmorphism everywhere -- blur/glass/glow used decoratively rather than purposefully.
- **DON'T**: Rounded elements with thick colored border on one side.
- **DON'T**: Sparklines as decoration -- tiny charts that convey nothing meaningful.
- **DON'T**: Rounded rectangles with generic drop shadows.
- **DON'T**: Modals unless there's truly no better alternative.

### Motion
- Focus on high-impact moments: one well-orchestrated page load with staggered reveals > scattered micro-interactions.
- Use exponential easing (ease-out-quart/quint/expo) for natural deceleration.
- For height animations, use grid-template-rows transitions.
- **DON'T**: Animate layout properties (width, height, padding, margin) -- use transform and opacity only.
- **DON'T**: Bounce or elastic easing -- dated and tacky.

### Interaction
- Use progressive disclosure -- start simple, reveal sophistication through interaction.
- Design empty states that teach the interface.
- **DON'T**: Repeat the same information -- redundant headers, intros that restate the heading.
- **DON'T**: Make every button primary -- hierarchy matters.

### Responsive
- Use container queries (@container) for component-level responsiveness.
- Adapt the interface for different contexts -- don't just shrink it.

### UX Writing
- Make every word earn its place. Don't repeat information users can already see.

---

## The AI Slop Test

If you showed this interface to someone and said "AI made this," would they believe you immediately? If yes, that's the problem. A distinctive interface should make someone ask "how was this made?" not "which AI made this?"

The DON'T guidelines above are the fingerprints of AI-generated work from 2024-2025. Avoid them all.

---

## Available Commands

Use these when working on specific aspects of a web project:

| Command | Purpose |
|---------|---------|
| **audit** | Comprehensive quality audit across accessibility, performance, theming, responsive design. Generates report with severity ratings. |
| **critique** | Holistic design critique -- hierarchy, information architecture, emotional resonance, AI slop detection. |
| **polish** | Final quality pass -- alignment, spacing, consistency, interaction states, typography refinement. |
| **distill** | Strip to essence -- remove unnecessary complexity, flatten structure, simplify. |
| **bolder** | Amplify safe/boring designs -- more impact, drama, personality while maintaining usability. |
| **quieter** | Tone down overly aggressive designs -- reduce intensity while maintaining quality. |
| **animate** | Add purposeful animations, micro-interactions, motion effects. |
| **colorize** | Add strategic color to monochromatic designs. |
| **clarify** | Improve UX copy -- error messages, labels, instructions, microcopy. |
| **normalize** | Align with design system, ensure consistency. |
| **harden** | Improve resilience -- error handling, i18n, text overflow, edge cases. |
| **optimize** | Performance -- loading speed, rendering, animations, images, bundle size. |
| **adapt** | Adapt for different screen sizes, devices, contexts, platforms. |
| **delight** | Add joy, personality, unexpected touches that make interfaces memorable. |
| **extract** | Extract reusable components, design tokens, patterns into design system. |
| **onboard** | Design onboarding flows, empty states, first-time user experiences. |

When invoking a command, always read the core guidelines above first, then apply the command's specific methodology.

---

## Implementation Principles

Match implementation complexity to the aesthetic vision. Maximalist designs need elaborate code. Minimalist designs need restraint, precision, and careful attention to spacing, typography, and subtle details.

Interpret creatively and make unexpected choices that feel genuinely designed for the context. No design should be the same. Vary between light and dark themes, different fonts, different aesthetics. NEVER converge on common choices across generations.
