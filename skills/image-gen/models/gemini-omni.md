# Gemini Omni Flash (Google)

Covers: `gemini` → `gemini_omni` (Higgsfield) / `gemini-omni-flash-preview` (Google direct)

Researched and written 2026-08-02. Sources listed at the bottom. Before this file existed, the
alias `gemini` was routed to `veo.md` as a placeholder — that was wrong: Veo and Omni Flash want
opposite prompting styles. See "Not Veo" below.

---

## What it actually is

A **natively multimodal** video model: text, images (and, on some routes, video) go in at the same
level, and it returns a short clip **with synchronized audio generated in the same pass**.

The headline feature is not the render quality — Veo 3.1 still beats it on a single polished
photoreal shot. The headline feature is **statefulness**: you generate once, then *converse*.
"Make the violin invisible." "Change the camera to over her shoulder." Everything you did not
mention stays where it was. No re-roll, no lost take.

**Mental model:** Veo is a slot machine you feed a perfect prompt. Omni Flash is a junior operator
who remembers the last take. The value lives in turns 2, 3 and 4 — not in turn 1.

---

## Hard specs

| | Google direct (official docs) | Higgsfield route (our CLI) |
|---|---|---|
| Duration | 3–10 s | presets **4 / 6 / 8 s** (`gemini_omni`) |
| Resolution | 720p, 24 fps | 720p native; platform offers 1080p upscale as post |
| Aspect | 16:9 (default), 9:16 | 16:9, 9:16 (platform also advertises 1:1, 4:5) |
| Audio | generated natively, in-pass | same |
| Cost | per Google plan/API | **3.0 credits/s**: 12 at 4 s, 18 at 6 s, **24 at 8 s** |
| Multi-turn editing | **yes** (`previous_interaction_id`) | **no — not exposed by our wrapper** |
| Watermark | SynthID, always on, not optional | same |

**The single most important line in this table** is the multi-turn row. Through
`scripts/generate.py` we get one-shot generation only, which throws away the exact thing this model
is best at. If the job needs conversational editing, it has to run on the **Google direct** route
(Vertex / Gemini API), not through our image-gen script.

Higgsfield's own marketing claims extension "to 60 s via continuation" and extra aspect ratios.
Treat those as *platform* features stitched on top, not model features. Google documents no video
extension or interpolation.

---

## The prompt formula

Six slots. Cover all six in the opening prompt; the model fills silence with its own choices.

```
[Subject]  — specific. "A woman in her early 30s, cream linen shirt" > "a person"
[Action]   — one clear action, in motion
[Setting]  — place + period + time of day
[Camera]   — shot size AND movement, in technical language
[Light/Style] — source-based, not mood-based
[Audio]    — say it out loud, or you get whatever it invents
```

Close with a format line: duration, aspect, and — critically — continuity.

**Length: 60–100 words for the opening shot.** Below ~40 it under-directs and invents. Above ~120
it starts averaging conflicting instructions. Edits afterwards should be **one sentence**.

### Worked example

> A woman in her early thirties, cream linen shirt, walks out of a metro exit into a crowded city
> square at golden hour. Medium tracking shot from behind, slow push-in, locked horizon. Late
> afternoon sun raking from camera left, long shadows across wet paving, warm tungsten from shop
> windows. Audio: street ambience, distant traffic, footsteps on stone. No dialogue.
> 8 seconds, 16:9, one continuous unbroken shot, no cuts.

---

## Techniques that measurably change the output

**1. Camera words are commands, not flavour.** `push in`, `punch in`, `pull back`, `orbit`,
`pan left`, `rack focus`, `dolly zoom`, `locked off`, `static`, `oner`, `one continuous shot`.
These parse. "Cinematic and dynamic" does not.

**2. Say "one continuous shot" or it will cut.** Left alone, Omni Flash edits itself — it will
insert its own cuts inside a 6-second clip. Use "in a single unbroken scene", "one continuous
shot", or "no scene cuts". This is the single most common surprise.

**3. Lighting by source, not by mood.** "Single practical lamp, rest of frame in shadow" beats
"moody atmospheric lighting" every time. Same for grade: "teal-orange grade, desaturated midtones,
shallow depth" beats "make it look cinematic".

**4. Lean on world knowledge instead of describing.** It knows eras, places, and cultural
references — "a 1970s Athens street kiosk" carries more accurate detail than forty words of
description, and costs you nothing in prompt budget.

**5. Timecode blocks for beat control.** Officially supported syntax:

```
[0-3s] A person walks toward the kiosk
[3-6s] They stop and turn to look off-camera
[6-8s] They start running
```
Plain language works too: "After 3 seconds, a woman enters the scene."

**6. Reference tags.** Official inline syntax on the Google route:
`<FIRST_FRAME>` = use this image as the opening frame. `<IMAGE_REF_0>`, `<IMAGE_REF_1>`… = subject
or style references, indexed from 0. Written into the prompt body:
`"in the style of <IMAGE_REF_0> a woman <IMAGE_REF_1> is walking"`.
Docs show up to 6 references in examples; no hard cap is published.

**7. Negatives go in the prose.** There is **no negative-prompt parameter** — nor system
instructions, temperature, top_p or stop sequences. Write exclusions as sentences:
"No dialogue. No on-screen text. No extra sound effects."

**8. Describe the audio in words.** Uploading audio references is **not supported** on the
official route (some resellers claim it is "rolling out" — do not build a job around it). Written
audio direction works: "Include calm background music", "the audio is a low tinny radio broadcast",
"she says, 'Welcome back'; soft studio room tone".

---

## Multi-turn editing (the actual craft)

Available on the Google direct route only. The discipline:

1. **Turn 1** — full six-slot prompt. Get the frame and the blocking right; ignore small faults.
2. **Turn 2+** — change **one variable**, and always append the lock:
   > "Change the jacket from red to navy. **Keep everything else exactly the same.**"
3. **Never re-describe the scene.** It remembers. Repeating context is how you accidentally
   re-roll the whole shot.
4. **Layer the passes** — camera, then action, then light, then grade. One per turn.
5. **Big changes get their own generation.** Scene swaps and heavy camera moves drift badly when
   done as edits; start fresh instead.

The lock phrase pattern generalises: `"[change]. Keep [X, Y, Z] identical."`

---

## Not Veo — do not carry Veo habits across

| Veo habit | What to do on Omni Flash |
|---|---|
| Long adjective-stacked paragraph | 60–100 words, six clean slots |
| Re-roll until it's right | edit conversationally, one variable per turn |
| Mood language ("dreamy, ethereal") | source language ("single practical lamp") |
| "shot on DJI Mavic Pro", gear names | motion verbs — "orbit", "pull back and rotate" |
| Named IP / studio styles | describe the technique ("hand-painted watercolour") |

Google's own docs now recommend **Omni Flash as the default video model** for coherence,
multi-input reasoning and character consistency — and Veo 3.1 only when you need scene extension,
last-frame control, or a legacy pipeline.

---

## Known failure modes

- **On-screen text is contested.** Google demos word-by-word animated text as a strength; working
  users report labels, signage and logos degrading badly. Verdict: short animated word cards in
  quotes are usable; **signage, logos and any non-Latin script are not**. Assume **Greek text will
  break** — build it in post.
- **Hands.** Fine articulation drifts — objects held, typing, instruments. Simple actions
  (walking, talking) are reliable; dancing, gymnastics and instrument-playing artifact.
- **Character consistency across 4+ shots** is mediocre without reference images. Lock the
  character with refs and name them consistently.
- **False-positive policy blocks.** Innocent prompts get refused; this is an acknowledged bug, not
  a prompt problem. Rephrase lightly or retry later — do not burn an hour rewriting.
- **Language.** Only English is fully evaluated. Prompt in English even for Greek-market work.

---

## Regional restriction that matters to us

**Editing uploaded video is not available to users in the EEA, Switzerland or the UK.** Verbatim
from the Gemini API Limitations section: *"Editing uploaded videos is not currently available for
users in the European Economic Area (EEA), Switzerland, and the United Kingdom."* The consumer help
page adds *"and some US states."* Editing *model-generated* video is fine everywhere. Uploading /
editing images containing minors is likewise blocked in those regions.

CooCoo operates out of Athens and London — both inside it. So "take our filmed plate, put it in,
change what's behind the actor" (the Xilouris-shaped job) is **exactly the workflow that is
restricted**. Text-to-video, image references and first-frame conditioning are unaffected.

**→ In practice this has been beaten: MM ran an uploaded-video edit from Greece on an Asia-exit VPN
on 2026-08-02 and hit no block.** Read the rest of this section for what that does and does not
settle.

**Scope check (2026-08-02):** the restriction is published in the *Gemini API* docs, not only the
consumer app — so it is an API-surface rule, not a consumer-app rule. Google's docs do **not**
state how region is determined, and do not carve out Vertex / Cloud. Treat "Vertex is exempt" as
**unverified** until a real call proves it.

### The three routes, ranked by how settled they are

| Route | Uploaded-video edit | Status |
|---|---|---|
| **VPN (exit node in Asia)** | **Works — field-verified** | **PROVEN 2026-08-02 by MM.** He ran Omni Flash from Greece on a VPN terminating in Asia, fed in a video, and changed things inside it: no block. This is the only route with a real result behind it. Open sub-question: which surface (browser app vs API) — see below. ToS exposure stands but the call is made. |
| Higgsfield (`gemini_omni`, our CLI) | `medias` array accepts media; identical schema to `seedance_2_0`, which *does* take video refs | Untested. Still worth the free cost-probe, because it would need no VPN and would work from our own scripts. |
| Google Cloud / Vertex (our own Cloud account) | Unknown | Enforcement basis undocumented. If it keys off the Cloud billing country and that country is Greece, a VPN changes nothing. |

**Superseded:** the earlier ranking here put VPN last on the strength of practitioner reports that
Google also keys off Google-account country. MM's run contradicts that in practice. The doc'd
restriction is real and still published; it is evidently not enforced hard enough to stop an
Asia-exit VPN. Treat the published rule as the *default* and the VPN as the *known working
exception*, not the reverse.

**Test that settles it, ~24 credits:** upload a ≤8s plate and call `gemini_omni` through Higgsfield
with the clip in `--video`, prompt = a background-only change with an explicit *keep the person
untouched* lock. Three outcomes: it edits the plate (route open), it ignores the clip and
text-to-videos (medias is images-only), or it errors on region (Higgsfield passes our geo through).
Cheaper diagnostic first: `higgsfield generate cost gemini_omni --prompt "..." --video plate.mp4` —
if cost estimation rejects the video param, the route is closed before spending anything.

**The validity test, and its answer.** The check on any "the VPN works" claim is *what* was
generated through it — text-to-video works in the EEA with no VPN at all, so a bare success proves
nothing. MM was asked and answered on 2026-08-02: he **put a video in and altered its contents**,
i.e. the restricted feature itself, not text-to-video. Test passed, question closed.

**Field result 2026-08-03 (MM, the Athens 1973 crowd shot).** A full uploaded-video edit ran and
MM's verdict was "worked really well": plate video in as the edit target, a still as the world
reference, prompt = lock the two leads and the camera, replace everything behind them. So the
pattern *hand it the clip, change only the background* is now proven end to end, not just the
permission for it. Two things this does **not** settle: which surface it ran on (see below), and
whether the plate's own production audio survives an edit — the prompt that worked carried an
explicit audio line, so nothing was left to chance.

**Still open, and it is an execution question, not a permission one:** whether that run was in the
browser app or through the API. It decides *how* a job gets done, not *whether*.
- **Browser app** — the restriction is beaten by hand, one clip at a time, and the multi-turn
  conversational edit (the model's real advantage, see above) is available. Nothing in this repo
  drives it; treat it as manual craft work, not a pipeline.
- **API through a VPN'd network** — scriptable, but `generate.py` sends one-shot prompts and does
  not carry a conversation forward, so the turn-by-turn refinement is lost. Would need work on the
  wrapper before it beats doing it by hand.

---

## Cost discipline

**Corrected 2026-08-07.** This section previously called Omni Flash "the second-priciest video model
in the catalog" and said 24 credits "puts it above Seedance 2.0 (22)". That compared an 8 s Gemini
clip against a 5 s Seedance clip. Per second, Gemini is **3.0 credits/s** and Seedance 2.0 is
**4.5 credits/s**, so Seedance is the dearer of the two, and Gemini sits mid-table: cheaper than
`h3` (4.0), `seedance` (4.5), `cinematic3` (5.0), `flux-video` (5.5) and `seedance2.5` (6.5),
dearer than `veo3.1` (2.75), `happy-horse` (2.5) and `kling` (2.0).

A full 8 s roll is still 24 credits, and with no multi-turn editing on this route a bad roll is
24 credits wasted, so the opening prompt has to carry all six slots. Always `--cost` first.

```bash
python skills/image-gen/scripts/generate.py "<prompt>" --video -m gemini --cost
python skills/image-gen/scripts/generate.py "<prompt>" -o /tmp/out.mp4 --video -m gemini -a 16:9 --duration 8 --user <telegram_id>
```

---

## Sources

Official (authoritative):
- https://ai.google.dev/gemini-api/docs/omni — generate & edit videos with Omni Flash
- https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash — model card
- https://ai.google.dev/gemini-api/docs/video — video generation overview, model-choice guidance
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/video-gen-prompt-guide
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-flash-preview

Practitioner (cross-checked, lower trust):
- https://geminiomniprompts.org/guide/ — formula, camera vocab, failure modes
- https://aiunfiltered.beehiiv.com/p/gemini-omni-prompting-guide-what-every-other-guide-gets-wrong
- https://roo.beehiiv.com/p/gemini-omni-prompt-guide-50-working-prompts-and-real-failure-fixes
- https://artlist.io/blog/veo-3-1-vs-gemini-omni-flash-how-google-just-changed-the-way-you-edit-video/
- https://morphic.com/resources/how-to/gemini-omni
- https://higgsfield.ai/gemini-omni-flash — the reseller route our CLI uses

**Contested points, flagged rather than resolved:** audio-reference upload (official: unsupported;
resellers: "rolling out"), text-rendering quality (Google: strength; users: weak), and clip
duration (Google: 3–10 s; Higgsfield: 4/6/8 presets; Higgsfield marketing: 60 s via continuation).
Nothing here has been verified by an actual generation on this machine.
