# Cinematic Studio Video (Higgsfield)

Covers: `cinematic3` (Cinematic Studio 3.0), `cinematic-video` (Cinematic Studio Video),
`cinematic-v2` (Cinematic Studio Video V2), `cinematic3.5` (Cinematic Studio Video 3.5)

These four are Higgsfield's own models. **None of them appear in `higgsfield model list`** any more,
which lists a curated subset, but all four still resolve and still generate. Checked 2026-08-07.

---

## Hard specs **[live 2026-08-07]**

| | `cinematic-video` | `cinematic-v2` | `cinematic3` | `cinematic3.5` |
|---|---|---|---|---|
| Duration | **5 or 10 s** | **3 to 12 s** | 5 s default | 15 s default |
| Aspect | 1:1, 4:3, 3:4, 16:9, 9:16 | 1:1, 16:9, 9:16 | 7 ratios | 7 ratios |
| Resolution | none | none | 480p, 720p, 1080p, **4k** | 480p, 720p, 1080p |
| Genre | none | 8 genres | 7 genres | 7 genres |
| Multi shot | no | yes, `multi_prompt` | yes, `multi_prompt` | yes, `multi_prompt` |
| Speed ramp | `slow_motion` bool | 6 ramp presets | 8 ramp presets | none |
| Style axes | none | none | none | **camera, light, grade** |
| Cost | 8 at 5 s, 18 at 10 s | **1.5 credits/s** std, 2.0/s pro | **5.0 credits/s** 720p | **5.0 credits/s** 720p |

`cinematic3` is 10 credits/s at 1080p and **24 credits/s at 4k**. `cinematic3.5` is 10 credits/s at
1080p. `cinematic-video` is the only model on the route with **non-linear pricing**: 1.6 credits/s
at 5 s but 1.8/s at 10 s.

**Corrected 2026-08-07:** the wrapper advertised a 10 s ceiling for `cinematic-v2`. The validator's
real ceiling is **12 s** ("Input should be less than or equal to 12"), so 11 and 12 second takes were
being refused by our own table rather than by the API.

**A caution on the two big ones.** `cinematic3` and `cinematic3.5` have **no upper duration check on
the cost endpoint**: quoting 999 seconds returns a price (4995 credits) rather than an error, unlike
every other model here, which rejects out-of-range durations outright. The same is true of
`marketing`. Treat a quote from these three as arithmetic, not as validation, and do not assume a
long duration will be accepted at generation time just because it priced.

### `cinematic3.5` style axes

3.5 is the only model here with named look controls, and they are **mutually exclusive with
`style_prompt`**: use the axes or the prose, never both.

- `camera_style`: classic_static, silent_machine, one_take, epic_scale, intimate_observer,
  impossible_camera, documentary_snap, raw_chaos, dreamy_flow
- `light_scheme`: soft_cross, contre_jour, overhead_fall, window, practicals, silhouette
- `color_grading`: naturalistic_clean, bleached_warm, hyper_neon, teal_orange_epic, sodium_decay,
  cold_steel, bleach_bypass, classic_bw

Both `cinematic3` and `cinematic3.5` default `prompt_language` to **`zh`**, not English. Pass
`--extra '{"prompt_language":"en"}'` when prompting in English.

---

## Prompt Style

Film-first direction. These models are tuned for cinematic video output. Write prompts as if briefing a cinematographer on a single shot.

## Optimal Length

50-100 words. Scene direction with mood anchoring.

## Key Techniques

- **Shot type first.** Open with the camera: "Wide establishing shot", "Tight close-up", "Over-the-shoulder medium shot." This frames everything that follows.
- **Color grade language.** Describe the look: "cool desaturated grade with teal shadows", "warm golden tones, lifted blacks", "high-contrast noir with deep blacks." The cinematic models respond strongly to color direction.
- **Motivated movement.** Camera movement should have a reason: "camera pulls back to reveal the empty room behind him", "slow push-in as she realizes what happened." Unmotivated movement looks arbitrary.
- **Atmosphere stacking.** Layer environmental elements: weather + light + texture. "Rain streaking the window, cold blue light from outside, condensation on the glass." Three atmospheric cues create a believable world.
- **Aspect ratio alignment.** Use 16:9 or 21:9 (if available) for the strongest cinematic feel. These models are optimized for widescreen compositions.

## What NOT to Do

- Don't mix documentary and narrative styles in one prompt
- Don't describe scenes that require VFX-heavy content (explosions, magic) without cinematic framing context
- Don't use quality keywords in place of actual direction

## Model Differences

- **cinematic3**: Cinematic Studio 3.0. Latest, best quality. Use for hero shots.
- **cinematic-video**: Cinematic Studio Video V2. Good general cinematic quality, slightly lower cost.

## Example Refinement

Bad: "cinematic video of a city at night, beautiful, dramatic"
Good: "Slow aerial drift over a nighttime city skyline, camera descending gradually. Sodium-orange streetlights below, cool blue office towers above. Light fog diffusing the edges. Teal and amber color grade, slightly underexposed. Distant traffic hum. Widescreen 16:9 composition, anamorphic characteristics."
