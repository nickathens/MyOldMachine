# Seedance (ByteDance)

Covers: seedance (Seedance 2.0), seedance1.5 (Seedance 1.5)

## Prompt Style

Structured scene direction with emphasis on lighting and movement separation. Seedance performs best when camera movement and subject movement are described independently.

## Optimal Length

50-120 words. Enough structure for the 6-step formula without overloading.

## Key Techniques

- **6-step formula.** Build prompts in this order: (1) Shot type and camera, (2) Lighting, (3) Environment, (4) Subject description, (5) Subject action, (6) Mood/atmosphere. Lighting is the most impactful element.
- **Lighting as primary lever.** Seedance is highly sensitive to lighting direction. "Single hard key light from camera-right, deep shadows on the left" produces dramatically different results than "soft diffused light". Always specify light source and quality.
- **Separate camera from subject.** Describe each independently: "Camera: slow push-in. Subject: turns her head to look out the window." Mixing them ("camera follows as she turns") produces ambiguity.
- **Speed through description, not words.** Instead of "fast running", describe the visual evidence of speed: "hair streaming behind, coat flapping, legs in full stride, slight motion blur." Avoid the word "fast" directly.
- **Name camera models.** "Shot on ARRI Alexa Mini" or "RED Komodo 6K look" produces more consistent cinematic texture than generic "cinematic quality".
- **Image-to-video.** When extending a still image, describe only what changes: "The subject blinks slowly and turns her head to the right. Background stays static. Camera locked off."

## What NOT to Do

- Don't use "fast" or "slow" as standalone speed descriptors (describe the visual effect instead)
- Don't combine camera movement and subject movement in one sentence
- Don't skip lighting direction (it is the strongest quality lever)
- Don't describe more than one scene per generation

## Model Differences

- **seedance**: Seedance 2.0. Best quality, strongest motion coherence, longer clips.
- **seedance1.5**: Seedance 1.5. Faster, lower cost. Good for iteration.

## Example Refinement

Bad: "man walking fast through a city, cinematic, dramatic lighting"
Good: "Medium tracking shot, camera dollying alongside at walking pace. Hard backlight from a low sun, long shadows stretching forward on the pavement. Downtown street, glass storefronts reflecting warm sky. A man in a dark overcoat, mid-30s, walking with purpose, briefcase in hand, coat hem swaying with each stride. Shot on ARRI Alexa Mini. Tense, urban, early morning energy."
