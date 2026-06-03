# Seedance (ByteDance)

Covers: seedance (Seedance 2.0), seedance1.5 (Seedance 1.5)

## The Master Rule

**Prompt length scales with shot complexity, not the other way around.**
The official ByteDance line: *"Simple prompts work for easy shots, more detailed prompts help with complex sequences."* Higgsfield publishes working production prompts at both 8 words and 800 words. The deciding factor is not length, it is **how many discrete beats the shot contains**.

Count the beats first, then pick the mode.

A beat is a discrete action with timing dependency on what came before. "Walks down stairs" is one beat. "Extends arms, paper falls, dissolves, second paper falls, dissolves, third paper falls, dissolves" is four beats.

## Two Prompt Modes

### Mode A — Single-beat shot (short, 50-100 words, 6-step formula)

Use when:
- Camera is locked or one continuous slow move (tilt, push, track)
- Subject performs one continuous action (walks, turns, looks up, breathes)
- Image-to-video with subtle changes (blink, hair sway, breath, lighting drift)

Build in this exact order:
1. **Camera** — shot size and one movement instruction (e.g., "locked wide, slow tilt down")
2. **Lighting** — direction + quality (e.g., "warm key from above, cool blue rim from behind")
3. **Environment** — anchor only what differs from start frame (e.g., "rain holds, fog drifts")
4. **Subject** — who they are, pointing at reference (e.g., "Harbor City fan boy from @image2, seen from behind")
5. **Action** — one physical verb in present tense (e.g., "walks slowly down the stairs")
6. **Style + Mood + Negative** — render style, emotional tone, 4-5 focused negatives

### Mode B — Multi-beat shot (timeline-structured, 150-800 words)

Use when:
- Sequential discrete actions with timing dependency
- Multiple camera positions or cuts in one clip
- Phase transformations (object dissolves, character changes, environment shifts)
- More than one element on a timing curve

Structure:
1. **Header block** — declare shot count and duration upfront ("Single continuous shot, 10 seconds, 16:9")
2. **Global anchors** — camera language, lighting recipe, environment, character references (each cited with explicit purpose)
3. **Beats** — use one of these two syntaxes, never both:
   - Timestamp blocks: `[0-3s]`, `[3-5s]`, `[5-7s]`, `[7-10s]`
   - Shot markers: `Shot 1:`, `Cut to:`, `Dissolve to:`
4. **One action per beat block** — never pack two simultaneous actions into one marker
5. **Global style + negatives** at the end, once

## The Full Cinema Block Order

Mode B is the short name for a discipline encoded as a fixed ten-block order, run on every complex or multi-character shot with no exceptions. Mode A is the same order collapsed: with one subject and one beat most blocks fold away and you are left with the six-step formula above. The moment a shot carries multiple characters, multiple beats, or needs maximum realism control, write all ten blocks in this exact sequence:

1. **Scene & Mood** — one or two lines: location, time, weather, a single emotional register.
2. **Frame Map** — pin every subject to a screen position, depth layer, and gaze *before* any identity or motion. Detailed below; this is the single biggest multi-character consistency lever.
3. **Subject Lock** — one block per character, physical only, trust the reference for wardrobe, restate only what changed, close with a hold line.
4. **Cross-Frame Rules** — the rails that hold the subjects in place relative to each other for the whole clip.
5. **Movement** — the beats. One action per beat, timeline or shot markers exactly as in Mode B.
6. **Last Frame** — the composition you want the clip to settle on at the end.
7. **World Plate** — what the environment and atmosphere do over the clip (the background's own behavior).
8. **Sound Bed** — diegetic audio only. Detailed below.
9. **Capture Realism** — the anti-plastic block. Detailed below.
10. **Camera Capture** — lens and light behavior, grain, grade. Behavior, never brand names.

**Density.** A full single cinema shot with every block present lands around 280 to 400 words; a multi-shot clip can run to roughly 600. Past that you are almost always stacking negatives or decorating. Positive locks ("gaze held to screen right", "same silhouette throughout") steer the model far harder than piles of prohibitions, so spend the word budget there.

### Frame Map — the multi-character win

Before you say who anyone is or what they do, place them. Pin each subject to a screen position (left third, center, right third), a depth layer (foreground, midground, background), and a gaze direction. In practice, the Frame Map did more to stop multiple characters melting into each other than any other single technique. Lock geography first, identity second, motion third.

### Subject Lock — one block per character

One block per person, physical description only, drawn from the locked identity spec (see `character-consistency.md`). Trust the reference image for wardrobe and restate only what has changed for this shot (damp, torn, dirt, sweat). Close every Subject Lock with an explicit hold line: "same face, hair, wardrobe, and silhouette throughout." Keep the canonical reference attached even when the character is plainly visible in the start frame — the reference is the anchor, the frame is only the first moment.

### Cross-Frame Rules — hold them in place

A short rule block applied to all subjects for the whole clip: no swapping positions, no crossing the center line, no changing depth layer, screen-sides and the gap between characters held constant. These are the rails that stop Seedance from quietly trading two faces or sliding characters past each other mid-clip.

### Capture Realism — the anti-plastic block

The four mechanics that separate footage that reads photographed from footage that reads rendered. State all four, every cinema shot:
1. **Depth through suspended atmosphere.** A thin veil of haze or particulate *between* the depth planes, not just in front of the subject, so foreground and background separate optically.
2. **Moisture without shine.** Only if the scene is genuinely wet: matte dampness, not glossy beads. Wet that does not glare.
3. **Per-zone specular kill on skin.** Name the zones and take the shine to zero: forehead, nose bridge, cheekbones, temples, chin. Pair it with a flattering ceiling so matte never turns ugly: no acne, no blemishes, no harsh enlarged pores.
4. **Contrast curve stated three ways.** Say it three times in three phrasings so the grade actually lands: lifted blacks, rolled-off highlights, low-contrast grade.

### Sound Bed — diegetic only

Seedance 2.0 generates sound, and that flips one rule hard: never put music, lyrics, or score in the prompt. Describe only diegetic audio, the sound the scene itself makes (footsteps on wet stone, distant crowd, rain on metal, a closing door). Music and score belong in the Higgsfield interface, never in the prompt text, or Seedance will try to synthesize a muddy version of it into the clip.

## Five Cinema Modes

Pick one mode per shot. They differ in movement, diffusion, grade, and palette, not in the Capture Realism register, which stays constant. Name the mode to yourself, then let it steer the Camera Capture and World Plate blocks.

- **M1 Narrative** — motivated, character-led moves; the default for story beats.
- **M2 Studio / Editorial** — controlled, clean, product- or portrait-grade light, minimal camera move.
- **M3 Action** — operator energy, handheld, faster beats, impact and weight.
- **M4 Performance / Concert** — stage and crowd, hard practical punch, rhythm-driven.
- **M5 Atmospheric / Empty** — landscape and mood with no lead subject; the air is the subject.

For night shots, run a hard-practical register: deep dark with sharp practical sources (the Tokyo Drift / The Batman / John Wick look), never milky bright-night and never teal-everywhere. The full night recipe lives in `character-consistency.md`; the same discipline carries over to motion.

## Image-to-Video Specifics

- **Do not redescribe what the start frame already shows.** Composition, lighting, character clothing, environment — those are anchored by the image. Describe only what changes.
- **Reference syntax:** `@image1`, `@image2`, etc. State each asset's purpose once (e.g., "@image1 start frame", "@image2 character reference", "@image3 paper reference").
- **Don't repeat the reference label five times.** Once per beat block at most.
- **For sequential image refs across a multi-shot clip:** the model will interpolate between them if you describe the action connecting them.
- **Up to nine references, in upload order.** Seedance accepts `@image1` through `@image9`, mapped to the order you upload them — so upload them in the same order you cite them, and state each one's job once.
- **Reference and start frame are different jobs.** A reference tells Seedance what a person or place looks like; a start frame is the literal first frame the motion grows out of. They are not always the same asset. Feeding a reference in as the start frame can lock you to its exact framing — sometimes the anchor you want, sometimes a cage. Decide per shot.
- **Name characters to the prompt-writer, never to Seedance.** Tell Claude the character names so it can track who is who across beats, but the prompt text itself must describe each character physically every time. Seedance has no idea who "Zara" is; a bare name is wasted or quietly hallucinated into someone else.
- **The rain-streak trap.** Never bake falling-rain streaks into the source still — Seedance reads them as texture and freezes them, so the rain hangs motionless for the whole clip. Give the still wet ground, reflections, and droplets on surfaces instead, and let the motion prompt carry any actual falling rain.

## Universal Rules (both modes)

- **Lighting first.** It is the strongest quality lever. Specify source direction and quality every time.
- **Volumetric depth by default.** Put something in the air in every shot — haze, particulate, light shafts, atmospheric falloff. Light the air, not just the room. Presence or absence of volumetric depth is the single biggest tell between footage that reads photographed and footage that reads generated, so even a clean interior gets a faint haze gradient.
- **One camera instruction per beat.** Stacking "handheld breath, no push-in, no zoom, micro-movement" creates jitter. Pick one.
- **Separate camera from subject.** Never write "camera follows as she turns." Write camera and subject in separate sentences.
- **No "fast" or "slow" as adjectives.** Show the visual evidence: hair streaming, motion blur, weight in the step.
- **Physical verbs over abstract.** "Melt", "fracture", "drift", "settle" beat "becomes", "transforms".
- **Negatives: 4 to 5 focused items.** Repeating "no facing camera" three times dilutes the signal. The three highest-leverage universal negatives are "no jitter, no bent limbs, no temporal flicker."
- **Describe lens and light behavior, not brand names.** Write what the glass and the light actually do — "shallow focus with soft falloff, gentle halation on highlights, slight distortion at the edges" — not "shot on ARRI Alexa Mini." Brand names make the model burn effort parsing jargon it only half-maps; optical behavior gives faster prompting and tighter control. (This reverses the older brand-name rule. Seedance 2.0 responds better to behavior — worth a quick A/B on your own shots to confirm before trusting it blind.)
- **Handheld realism is a camera choice, not a garnish.** A subtle operator shake or a slight Dutch tilt sells "a real person holding a real camera," but it counts as the one camera instruction for that beat. Do not stack it on top of a push or a tilt.
- **No decorative adjective stacks.** "Sacred, reverent, mythic, hushed, sacred" four times each eats tokens and tells the model nothing visual. Pick one mood word and let the lighting carry the rest.

## What Causes Failures

1. **Packing multiple sequential actions into one paragraph.** The model has no temporal scaffold and either collapses them simultaneously or renders only the first.
2. **Repeated negatives.** "Never face camera, no front-facing pose, no turn toward lens, no facing camera" = four times the noise, not four times the constraint.
3. **Camera + subject in the same sentence.** Produces ambiguity, often jitter.
4. **Multiple stacked camera commands.** "Handheld, no push-in, no zoom, subtle breath, locked off" — pick one and commit.
5. **Redescribing the start frame.** Wastes tokens and competes with the image for authority.
6. **"Fast"/"slow" unqualified.** Causes chaos in motion planning.
7. **More than one scene or location per generation.**
8. **Music or score in the prompt.** Seedance 2.0 synthesizes audio from the prompt text, so any music, lyrics, or score gets rendered as a muddy bed you cannot mix. Keep music in the Higgsfield interface and describe only diegetic sound.
9. **Naming who is who without placing them.** With two or more characters, skipping the Frame Map lets the model swap faces or merge them. Pin position, depth, and gaze before identity.

## Model Differences

- **seedance**: Seedance 2.0. Best quality, strongest motion coherence, supports auto-duration 2-12s, multimodal references.
- **seedance1.5**: Seedance 1.5. Faster, lower cost, has audio synthesis. Good for iteration.

## Render Cheap, Upscale After

Proof at 720p, upscale only the selects. Seedance credits scale with resolution, so on a multi-take shot you burn far fewer credits by validating motion and composition at 720p and upscaling only the keepers. For video, Topaz Video is the common upscaler; the local upscale skill is image-only (Real-ESRGAN), so for footage you would upscale extracted frames rather than the clip itself.

## Examples

### Single-beat (Mode A) — locked wide shot, boy walks down stairs

Camera: locked wide on the harbor from @image1, slow gentle tilt down to find the boy on the stairs.
Lighting: cool moonlight, warm tungsten practicals, volumetric haze.
Environment: background holds from @image1, soft rain and fog continue.
Subject: Harbor City fan boy from @image2, seen only from behind, hood up.
Action: walks slowly down the stairs toward the arena, one careful step at a time, never turns.
Style: high-end Arcane 2D painterly hand-drawn.
Mood: mythic, melancholic, hopeful.
Negative: no front-facing pose, no glance over shoulder, no climbing up, no fast tilt, no jitter.

(~110 words. One beat. Works.)

### Multi-beat (Mode B) — papers fall and dissolve into hands

Single continuous shot, 10 seconds, 16:9.
References: @image1 start frame, @image2 paper reference, @image3 character sheet.
Camera: locked handheld interior, subtle breath, no push-in.
Lighting: warm golden key from above on face and palms, cool blue rim from sealed doors behind. A brief golden bloom washes his palms each time a paper dissolves.
Environment: background holds from @image1.
Subject: Harbor City fan boy from @image3, hood already off, gaze held upward to screen right throughout.

[0-3s] Boy slowly extends both arms outward, palms turning up at chest level.
[3-5s] First small ember-edged paper, sized like @image2, drifts down onto left palm, settles, dissolves into golden embers fading into his skin.
[5-7s] Second paper drifts down onto right palm, settles, dissolves the same way.
[7-10s] Third paper drifts down onto palm, dissolves; gaze held upward in stillness.

Style: 2D Arcane painterly hand-drawn animation.
Mood: sacred, hushed, reverent receiving.
Negative: no sudden motion, no bursts, no large papers, no front-facing pose, no jitter.

(~230 words. Four beats. Each beat gets its own time block. Works.)

### Full block order (Mode B, cinema) — two fans, photoreal, multi-character

Single continuous shot, 8 seconds, 16:9.
References: @image1 start frame (arena stands), @image2 older fan, @image3 younger fan.

Scene & Mood: Harbor City home stands minutes before tip-off, black and Harbor City yellow everywhere, the hush of held breath under arena light.
Frame Map: two fans. Older fan in the left third, midground, facing the court at screen right. Younger fan in the right third, foreground, turned inward toward the older fan. They never cross the center.
Subject Lock A: older fan from @image2, weathered face, grey stubble, yellow scarf. Trust the reference for wardrobe; same face, hair, scarf, and silhouette throughout.
Subject Lock B: younger fan from @image3, late teens, short dark hair, black Harbor City jersey. Trust the reference; same face, hair, jersey, and silhouette throughout.
Cross-Frame Rules: no swapping sides, no crossing center, no depth change; the gap between them and their screen-sides hold for the whole clip.
Movement:
[0-3s] younger fan looks up from his seat toward the older fan.
[3-6s] older fan lifts his gaze slowly to the court at screen right.
[6-8s] both hold the look, stillness settling.
Last Frame: both fans still, older fan's eyes locked on the court, younger fan watching him.
World Plate: stands stay packed and softly out of focus behind them, faint banner sway, dust motes drifting through the light.
Sound Bed: low arena murmur, a distant whistle, a single seat creak. No music.
Capture Realism: thin haze between the stands and the foreground so the planes separate; matte skin, zero shine on foreheads, nose bridges, cheekbones, temples, chins, no blemishes; lifted blacks, rolled-off highlights, low-contrast grade.
Camera Capture: slow handheld push, shallow focus with soft falloff, gentle halation on the highlights, fine 35mm grain.
Mood: reverent, charged, waiting.
Negative: no face swap, no crossing center, no jitter, no bent limbs.

(~330 words. All ten blocks. Two characters held apart by the Frame Map and Cross-Frame Rules. Works.)

## When in doubt

Count the beats. One beat → Mode A. Two or more discrete sequential beats → Mode B with timeline structure. If the prompt feels right at 80 words for a complex sequence, you have likely collapsed beats and Seedance will fail. If it feels bloated at 600 words for a single locked shot, you are stacking negatives and decorative adjectives — strip them.
