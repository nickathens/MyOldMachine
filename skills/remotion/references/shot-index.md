# Shot index

104 motion recipes, English gloss, grouped as upstream groups them. Each row points at `shots/<category>/<name>.md`, which is the real card: intent, motion core, a parameter table with feel notes, and a known pitfalls section. The cards are in Chinese. Read the card before implementing, not just this line.

Energy is the card's own rating and it matters more than it looks. A film is ruined by stacking high energy shots, so the cards repeatedly cap themselves ("at most one per video"). Respect those caps.

Nothing here is implemented yet except `odometer-digit-roll`, which shipped as the `MetricStomp` composition. Treat the rest as a menu, not a library.

---

## camera (7)

| card | energy | what it does |
|---|---|---|
| `crash-zoom-punch` | high | Wide snaps to a close up in 6 frames, then either overshoots and springs back or slams to a stop with a screen shake. The "point at this" shot. |
| `depth-layer-moves` | medium | Two ways to give a flat screenshot thickness: a three layer parallax dolly, and a fake dolly zoom where the subject is pinned and the background swells inward. |
| `graze-face-tour` | medium high | Camera flies low across a UI surface at a steep angle, treating sidebars and lists as terrain. Text floats above the surface with a matching soft shadow and lands as the camera passes. |
| `overhead-camera-moves` | medium | Top down storytelling: a tilt that rights itself into a reveal, or a table of cards that slides then drops the camera straight into one. |
| `space-camera-moves` | high | Treats a flat page as a 3D solid: an exploded view that blows apart along Z and reassembles, and a drone dive that lands. Big gestures, at most two per film. |
| `steep-tilt-glide` | medium high | Static camera, page standing at a 60 degree perspective, and the page itself slides past with speed ghosting while components settle onto it. Dark scene, neon edge. |
| `tension-camera-moves` | varies | Four emotional moves: bullet time orbit, dutch roll righting itself, slow oppressive push, and a pull back that isolates. Small motion, heavy feeling. |

## data (8)

| card | energy | what it does |
|---|---|---|
| `before-after-slider-scrub` | medium | A divider whips across a stacked before and after pair, developing the new version in its wake. |
| `chart-live-moves` | medium high | Charts that behave as living instruments: an oscilloscope writing at the right edge with spikes, a unit dot swarm, and relatives. |
| `gauge-readout-moves` | medium high | Mechanical instruments: a needle sweeping the full arc as a power on self test before falling to the true value, and a scrolling tape readout. |
| `odometer-digit-roll` | medium high | One huge metric fills the frame, each digit rolling as an independent slot reel, locking left to right, and the whole group pulsing on the final lock. **Implemented as `MetricStomp`.** |
| `particle-celebrate-hits` | accent only | Confetti crossfire on a milestone frame, and a counter tick spark. Garnish, never structure. |
| `particle-sand-fill` | medium high | A bar chart that does not grow. It rains: square particles fall and pile into the column, then solidify and pop the value. |
| `scroll-brake-moves` | high into medium | A long scroll travelling fast, decelerating exponentially into an exact stop with the target lifting. A second variant adds a rebound. |
| `timeline-travel` | medium high | The camera accelerates along a horizontal time axis, each version tick popping a card up as it passes, hard stopping and pushing in on the last. |

## effects (10)

| card | energy | what it does |
|---|---|---|
| `brand-frame-snap` | medium | A thick brand colour frame grows around the whole screen first, the screen recording drops inside it, and on a mode change the frame and the contents flip colour on the same frame. |
| `fui-hud-moves` | medium | Heads up display grammar: a line unfolding into a panel with CRT feel, and a reticle locking on. |
| `glow-flyline-moves` | low to medium | Dark field atmospherics: ambient glow orbs, arcing fly lines that connect two points, and the two combined. |
| `icon-performance-moves` | accent only | An icon that performs: a checkmark winding up, popping oversized, bursting particles and throwing a ring; and an attention bounce. |
| `impact-feedback` | high | Hit feedback: a combo counter with frame hold and damage numbers, and an anime impact frame with a negative flash. |
| `light-play-moves` | medium | Three lighting gestures: a spotlight sweeping across type, a single point sheen, and a halation bloom on a hard stop. |
| `line-boil` | low, ambient | During a hold, outlines wobble slightly every 3 frames, as if redrawn by hand. Keeps a still frame breathing. |
| `riso-print-hits` | high | Risograph misregistration: a hard impact frame that splits into two ink plates, shivers twice and registers. |
| `slam-entrance-moves` | high | Three high energy arrivals, including a Kanada style perspective snap and a score slam. |
| `spotlight-sweep-moves` | medium low | Restrained dark field developing: light wakes what it touches and lets it sleep again behind, a purple wash bleeding along a UI edge, and a slow corner reveal. The most cinematic set here. |

## interaction (11)

| card | energy | what it does |
|---|---|---|
| `ai-stream-response` | medium high | An AI panel lands one readable summary sentence first, then evidence rows with status icons flow in one by one, then everything resolves into a completed state. |
| `autolayout-gap-dial` | medium | A spacing dial drives real layout: badge numbers tick, blocks are pushed apart live by the parameter and spring back. Parameter driven layout made visible. |
| `canvas-materialize-moves` | medium | Content becoming physical on a canvas: a table row flying out along an arc and morphing into a card in another container. |
| `collab-cursor-moves` | medium | Cursors as actors: two cursors performing a dark field pas de deux, and a cast entrance. |
| `command-palette-summon` | medium, ceremonial | The screen dims and blurs, a command palette drops in with overshoot, candidate rows stagger in, and the list narrows live as characters are typed. |
| `hashtag-to-pill-materialize` | medium | A hashtag is typed centre screen, hard cuts into a wide pill on a single frame, then shrinks and flies to its place in the page. |
| `input-trigger-moves` | medium | Input as the trigger: a cursor performing a click with a push in, and a keycap smash cutting the scene. |
| `segmented-thumb-hero` | medium | A segmented control's thumb as the hero of a macro shot: oversized pill springs in, an outlined arrow cursor slides in from offscreen and presses. |
| `theme-switch-moves` | medium | Two theme switches: a diagonal sweep that reskins in place behind its boundary, and a palette ripple out of a collapsing command palette. |
| `type-and-filter` | medium | Typing a search into a real UI, the grid converging to a single card, and the click punching through into the detail page. |
| `voice-waveform-live` | medium | A recording pill with 64 live bars: tall in the middle while speaking, collapsing to dots on a pause, scrolling right to left, and collapsing on submit. |

## opening (9)

| card | energy | what it does |
|---|---|---|
| `brand-ink-open` | low | An ink crosshair draws itself, the wordmark stamps in letter by letter, a typewriter subtitle follows, a full second of stillness, then it floats away. |
| `crane-rise-reveal` | medium high | Opens tight on one row of data; the camera cranes up and back, rows pouring in until the whole dashboard fills the frame. One continuous move. |
| `dataviz-landscape-open` | low, rising | Dark field. Tributary flow lines converge into a trunk, fictional ID labels float above them, the camera flies slowly through heavy depth of field. |
| `icon-field-colorize` | medium | A grey icon field staggers in until it fills the screen, holds one beat, then bands of brand colour rip down and flip the whole field to colour. |
| `letterspace-materialize` | low, ceremonial | A wide tracked wordmark crystallises: every letter starts on the same frame, strokes grow as if handwritten, all finishing together. |
| `magician-card-flourish` | high, once only | A blue star burst flares for 0.3s on pure black, then a card ejects from the flash point, spinning fast along an arc toward the camera. |
| `spotlight-hero-card` | medium, highest texture | A spotlight sweeps the page and locks a card; a 45 degree push in, the card lifts and hovers, the beam traces its outline twice, then it sits back down. |
| `stroke-segment-build` | low, rising | A title is broken into a dozen disconnected strokes lit in random order. Unreadable for the first 70 percent, then the last stroke lands and the meaning snaps into place. |
| `text-as-mask` | medium high | Product footage plays inside an ultra heavy headline, then the letterforms scale up 26 times until the footage takes the whole frame. |

## outro (5)

| card | energy | what it does |
|---|---|---|
| `edit-hook-moves` | low, then a spike | End card hooks: a 12 frame sting cut in after the logo settles, and a trailer button ending. |
| `neon-triple-marquee` | medium high | Three rows of hollow outlined giant type scrolling in alternating directions, filling the frame, as a recap. |
| `outro-group-photo-launch` | peak | Every element in the film flies in from all sides to surround the wordmark for a group photo, with a crane landing, stage light and gold dust. |
| `ui-strip-away-outro` | medium | Subtraction ending: after Publish is clicked, the whole editor evaporates from the outside in, leaving one button that slides to centre and grows. |
| `ui-to-brand-morph` | medium high | The UI becomes the brand: an icon flips on Y into a line, blooms into the mark, and the wordmark lands letter by letter. |

## rhythm (10)

| card | energy | what it does |
|---|---|---|
| `beat-cut-moves` | high | The hard cut used as a percussion instrument: an accelerating chain where the interval halves each time, and a triple flash freeze. |
| `beat-step-list-theme-cycle` | high | A three channel metronome: an adjective list stepping up one row per beat, a fixed centre pill catching the next word and changing colour, and the field colour changing on the same beat. |
| `montage-rhythm-moves` | high | Three montage devices: a blackout that charges before the blast, a Wright style triple cut, and a domino. |
| `panel-grid-moves` | high | Grid rhythm: a nine panel flash mosaic filling and swallowing the frame, a grid reflowing as a group, and a comic panel variant. |
| `rhythm-interrupt-moves` | medium | Interruption as rhythm: a three step jump cut push in, and strobing black frames. |
| `sakuga-timing-shift` | medium high | Elements move on threes like a flipbook, then snap to every frame for the climax sprint. The change in frame rate is itself the effect. |
| `smear-multiples` | medium high | A card moving fast drags four countable semi transparent copies that collapse into one on landing. The animation native alternative to motion blur. |
| `spectrum-morph-ui` | medium | A title's underline splits into a bar spectrum, bounces for two bars, and collapses back into a line. |
| `speed-ramp-freeze` | medium high | Non linear frame remapping: fast, then 0.2x for a stare, then fast; plus a freeze and annotate variant. |
| `trailer-grammar-moves` | high | Trailer grammar: a fast cut hook before the title, title cards interleaved with dialogue, and a smash cut. |

## transition (15)

| card | energy | what it does |
|---|---|---|
| `bottom-push-stack-wipe` | medium | The new scene, its background colour included, pushes up from the bottom edge and physically shoves the old one out of frame. |
| `bubble-swarm-takeover` | medium high | Pearlescent bubbles drift in and swell until they cover the frame, the cut hides at peak occlusion, and they disperse onto a new scene. |
| `card-flip-reveal` | medium high | A feature card flips 180 degrees on Y; a highlight band tracks the angle across the thinnest edge, and the back reveals a large conclusion number. |
| `card-flock-tumble` | very high, once per film | Three page cards tumble in from their thin edges into a staircase, keep slowly rotating, then rush together and are sucked into the centre. |
| `circle-match-iris` | medium high | An iris opens from the centre of a circular element on the page, and a circular chart on the new page continues that same circle. A match cut with a semantic anchor. |
| `color-block-step-wipe` | medium high | A colour block eats the screen in 3 to 5 discrete hard steps rather than a smooth wipe. |
| `line-carry-transition` | medium | A progress bar extends off frame, the camera tracks along it, and the same line bends around to draw the card frame of the next scene. No cut at all. |
| `page-turn-transitions` | medium high | Whole page solids: two pages on adjacent faces of a cube rotating 90 degrees, and a barn door split. |
| `paper-plane-messenger` | medium | After Send, the camera pulls out of the window, a paper plane flies a bezier arc with its pitch following the tangent, and the camera flies with it through parallax layers. |
| `print-texture-transitions` | medium | Print texture: an ink bleed with feathered fingers spreading to eat the old scene. |
| `shot-transitions` | technique | Six handovers: push through white, straight through darkness, a focus pull relay, a black title card, a whip pan, and a mask wipe. Pick by energy. |
| `tear-streak-transitions` | high | A glitch displace tear: 16 horizontal bands offset and shudder, and the cut hides inside. |
| `transition-hidden-cut` | technique | Three invisible cuts: a foreground object wiping past, two objects colliding open, and a warm light leak. The scissors hide in 1 to 3 frames. |
| `transition-travel` | technique | Travelling transitions: a shared element returning to place, and flying through the counter of a letterform. The camera enters a real object in the frame. |
| `wipe-transitions` | medium | Geometric erasure: a clock wipe sweeping like radar, and 12 staggered venetian blind slices. |

## typography (14)

| card | energy | what it does |
|---|---|---|
| `cel-flash-stomp` | high | Big words stamp in crooked, one per beat, and on each landing frame the background layer strobes between two flat colours for a few frames while the text stays perfectly still. Anime finishing move grammar. Sound is not optional here. |
| `document-typewriter-reveal` | low to medium | A full page of real typeset document writes itself behind a cursor, the sidebar keeping pace, history entries dropping into their track. |
| `gradient-word-sweep` | high, one word | A gradient charge sweeps a keyword left to right, brightest at the wavefront, with fine lightning between characters and a steady glow breath once full. |
| `marker-underline-title` | low, one gesture | After the headline lands, a marker underline draws fast under the keyword: variable width, ragged edge, slightly rising to match the italic. |
| `paper-title-card` | low, breathing | A sentence stamps onto paper word by word, one word italic in the accent colour, closed by a short rule. |
| `pill-slot-cycle` | medium, steady | The sentence stem stays nailed in place; the pill badge at the end rolls one slot every 0.7s, the old word flying up and out, the new sliding in from below with blur. |
| `split-flap-title` | medium, mechanical | An airport split flap board: every character flips through two garbage glyphs and clacks onto the target, cascading left to right. |
| `text-column-converge` | medium low | Two words face off at equal margins, hard cutting through variants with zero movement, and only the final word ever eases in to close the gap. |
| `title-demote-to-label` | low to medium | A centred headline holds a beat, then shrinks 0.3x and travels to the top left as a section label while the content grows underneath. |
| `type-assembly-moves` | medium | Four ways type assembles: characters splitting and rising, letterforms drifting together, and two more. |
| `type-entrance-moves` | medium high | Two title entrances: a scramble decode where the answer grows out of noise, and letters dropping with physics. |
| `type-rhythm-sync` | high | Type locked to sound: a font weight pump where strokes thicken on the kick, and a karaoke fill sync. |
| `typewriter-moves` | medium high | Two typewriters: a terminal command that detonates the scene the moment it finishes, and an error retype that visibly changes its mind. |
| `word-relay-filmstrip` | medium low | A left column of alternating page cards steps down while a serif keyword relays in place on the right, the noun constant and the verb rotating. Editorial feel. |

## ui-entrance (15)

| card | energy | what it does |
|---|---|---|
| `cloner-depth-echo` | medium | The main card instantly photocopies seven translucent clones into a diagonal column of depth, holds a beat, then sucks them all back with a bounce. |
| `deck-deal-flyin` | high, rising | A macro orbit around a physical deck on dark metal, then the camera pulls back and cards are dealt hard into a grid, the camera chasing the scroll. |
| `draw-svg-trace` | medium | An ink line with a visible nib runs the outline of an element to draw it into being; on closing the loop it flashes black and hands over to the content. |
| `element-body-moves` | medium high | Giving elements a body: an axial stretch like pulled syrup, and a contact shadow lifting off the surface. |
| `integration-hub-map` | medium high | The old page flips 180 degrees, its edge flashing, and lands as a hub; five app icons pop on the same frame and five colour light pipes connect at once with pulses running through them. |
| `list-stack-press` | medium | List cards fly up from the bottom edge and stack, each landing compressing the whole stack while a counter ticks. |
| `morph-from-primitive` | medium low | A perfect circle breathes once as anticipation, then an SVG path interpolates over 24 frames into a rounded card outline and the content fades in. |
| `neon-frame-forerun` | medium high | A hard perspective neon frame races ahead from the left edge and forms first; the page brightens inside it while components fall onto it from above with matching soft shadows. |
| `neon-frame-orbit-drop` | medium high | Same neon frame, then the camera arcs left to right while every component drops onto the page on the same frame. An ensemble entrance rather than a stagger. |
| `page-waterfall-wall` | medium | Real screenshots sliced into 3 or 4 columns scrolling infinitely at different speeds in opposite directions on a leaning 3D wall. Reads as "more content than can fit". |
| `paper-craft-moves` | medium | Paper craft: masking tape slapped down over a floating element to pin it, and a pop up book rise. |
| `row-embed` | medium | A content row descends from above, rotates flat on X, and the moment it seats, a bright accent seam lights along its bottom edge. |
| `runway-ground-skim` | high | A low angle skimming the ground while UI cards fall in a hard shower, overlapping heavily, stopping dead on contact with zero bounce; then the page stands up and the view rights itself. |
| `skeleton-reveal` | medium, narrative | Three stage development: hand drawn boiling scribble placeholders, replaced by grey skeleton bars, then the camera pushes in and the bars resolve row by row into avatars and words. |
| `wall-reveal-moves` | medium | Whole wall entrances that never move anything: bento cells lighting up in order, a grid flipping as a wave, and a blueprint drawing itself. |
