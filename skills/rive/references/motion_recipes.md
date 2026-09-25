# Motion recipes

Patterns for the motion people ask for, in RML. Each says where it was proven: a template in `templates/`, a measurement on this Mac, or Rive's docs (not yet built here). Property keys are in `rml.md`.

## Easing

A keyframe's `interpolationType` shapes the segment that STARTS at it; the last key's type is never read, and `hold` is the default (a slideshow). `cubic` needs a curve child:

```xml
<KeyFrameDouble value="0" frame="0" interpolationType="cubic">
    <CubicEaseInterpolator x1="0.16" y1="1" x2="0.3" y2="1"/>
</KeyFrameDouble>
<KeyFrameDouble value="1" frame="24" interpolationType="hold"/>
```

| Feel | x1 y1 x2 y2 | Used in |
|---|---|---|
| expo out: fast arrival, long settle (UI, supers) | 0.16 1 0.3 1 | lower_third, ui_screen, logo_reveal fill |
| in-out: travel between two rests (scrolls, draw-on) | 0.65 0 0.35 1 | ui_screen scroll, logo_reveal draw |
| ease out, gentle | 0 0 0.58 1 | |
| accelerate away (exits) | 0.5 0 0.84 0 | lower_third out |
| CSS ease | 0.25 0.1 0.25 1 | |

`elastic` with an `ElasticInterpolator` (`amplitude`, `period`) overshoots and settles; `cubicValue` with a `CubicValueInterpolator` lets the value swing past its keys (y1/y2 are in the property's units). The same interpolators ease state transitions and `DataConverterInterpolator`s.

## Loops without a pop

A `loop` jumps from the last frame to the first, so the two must hold the same value (rotation 0 to 2π is seamless, opacity 1 to 0.3 is not). Either key a symmetric third keyframe or use `loopValue="pingPong"`, which reverses and is seamless by construction. Put an idle loop on its own state machine layer with no conditions; it runs forever beside the interactive layers (the `ui_screen` caret blink). Keep loop lengths away from whole seconds when frames are sampled at whole-second spacing: a 1 s loop sampled every second shows the same phase every time (seen on the first demo).

## Type-on and cascades (lower_third)

```xml
<TextModifierGroup modifyOpacity="true" modifyTranslation="true" invertOpacity="true"
                   opacity="0" y="26" name="Reveal" id="0:32">
    <TextModifierRange modifyFrom="-0.25" modifyTo="1" falloffFrom="0" falloffTo="1" name="Sweep" id="0:33"/>
</TextModifierGroup>
```

Key the range's `modifyFrom` (327) from `-0.25` to `1` and `falloffFrom` (317) from `0` to `1.25` over the same frames, with the same ease: every glyph starts hidden 26 units low and each fades and rises in as the ramp passes it. Starting `modifyFrom` below 0 is what hides the first glyph on frame one. `unitsValue` 2 cascades by word, 3 by line. Without `invertOpacity`, opacity multiplies and the whole text disappears.

## A plate that hugs its text (lower_third)

A `hug`/`hug` column `LayoutComponent` with padding (each with `*UnitsValue="points"`), a `Fill`, and the texts as `LayoutParticipant` children: the plate grows with a longer name, which is what makes one super template serve every name. Key the plate's `scaleX` from 0 with a `ComponentOrigin originX="0"` to wipe it open from the left. Pin the whole thing with an absolute position (`positionLeft`, `positionBottom`).

## Draw-on (logo_reveal)

```xml
<GroupEffect name="Draw" id="0:81">
    <TrimPath start="0" end="0" name="Draw Trim" id="0:82"/>
</GroupEffect>
<!-- in each stroke that should draw on -->
<TargetEffect targetId="0:81" name="Draw On"/>
```

Key `end` (115) on the one `TrimPath` from 0 to 1 and every stroke pointing at the group draws on together. `rive_svg.py --reveal` builds this around any SVG: a stroke-only line copy, a feathered glow copy and the original fill, cross-faded.

## Glow (logo_reveal, visualizer)

Two strokes on one shape: the soft one first (thick, with a `Feather`), the crisp one after (drawn on top). A glow ring whose opacity is bound to a value flashes with it (the visualizer's `onset`). A filled glow is a `RadialGradient` from the colour to the same colour at alpha `00`; `Feather` inside a `Fill` renders nothing.

## Soft drop shadow (measured)

Rive has no shadow effect. A thick feathered stroke on a slightly smaller copy of the shape, offset down, declared after the card so it sits under it:

```xml
<Shape x="300" y="200" name="Card">
    <Rectangle width="320" height="200" originX="0.5" originY="0.5" cornerRadiusTL="24" name="Path"/>
    <Fill name="Fill"><SolidColor colorValue="FFFFFFFF" name="C"/></Fill>
</Shape>
<Shape x="300" y="200" name="Card Shadow">
    <Rectangle width="300" height="180" originX="0.5" originY="0.5" cornerRadiusTL="24" name="Path"/>
    <Stroke thickness="40" name="Shadow">
        <SolidColor colorValue="55000000" name="C"/>
        <Feather strength="28" offsetX="0" offsetY="18" name="Soft"/>
    </Stroke>
</Shape>
```

## Counters (counter)

Text bound to a number through `DataConverterInterpolator` (eases changes over `duration` seconds) then `DataConverterToString` (`decimals="0" round="true"`), with `TextStyleFeature tag="1953396077"` (`tnum`) so digits do not jump. A progress ring is a `TrimPath` whose `end` is bound through a `DataConverterFormula` (value divided by a bound `goal`), clamped by a `RangeMapper`. The interpolator only eases changes after binding: in a render the count must come from a curve (`rive_recipes.py count --path value --to 1250`), on a live page setting the value eases by itself.

## Typing with a caret (ui_screen)

Bind the text run to a view model string and put a thin `LayoutComponent` caret after the text in a `row` layout: as the string grows, the caret moves with it. Drive the string per frame with `rive_recipes.py typing "harbour at dawn" --path query -o typing.json` (a seeded human rhythm with pauses after spaces and punctuation) and `rive_render.py --timeline typing.json`. Blink the caret on its own looping layer (opacity 1, then 0 at frame 30, both `hold`). For a web page with no host code, bake the typing into the file as `KeyFrameString` keys on the run instead: `rive_recipes.py keys "text" --object <run id>`.

## Lists and scrolling (ui_screen)

Rows come from data: an `ArtboardComponentList` bound to a view model list repeats a component artboard per item. For a scripted, repeatable scroll in a render, key the list container's `y` inside a `clip="true"` viewport so rows slide under the edge instead of over the header. For a real touch scroll on the web, use a `ScrollConstraint` with `ElasticScrollPhysics` (friction about 2.5 feels like a phone; the default 8 is heavy) and render it with `--drag`: in capture mode a fling is deterministic and its step count is its speed. A `ViewModelPropertySymbolListIndex` (`symbolTypeValue="itemIndex"`) gives each row its index for staggered entrances (Rive docs; not built here yet).

## Hover, press and toggle (button)

Three layers, one boolean each (`hover`, `down`, `on`), written by listeners (`enter`/`exit`/`down`/`up`/`click`) and read by transitions. States are one-key animations; the transition `duration` (ms) is the cross-fade. Hover and press key different properties (scale and y), because two layers keying the same one fight. The toggle writes the negation of its own value (read context with `DataConverterBooleanNegate`, write context `direction="true"`), and has transitions both ways; `rive_check.py --interaction click@210,70` proves it returns. Labels switch by keying the run's string (`KeyFrameString`) and colour (`KeyFrameColor`), not with a `Solo` of texts.

## Music (visualizer)

Bars bound to `b1..b12` through one `RangeMapper` (0..1 to pixels, clamped so a spike cannot break the frame), a ring whose `scaleX`/`scaleY` follow `low`, a feathered glow whose opacity follows `onset`, an aura whose opacity follows `level`. The values come from `rive_audio.py track.wav --log-bands 12` and arrive per frame with `--curves`. Smoothing belongs in the analysis (attack/release), not the file: on the CLI engine every frame is a fresh run, so the file sees one value per frame.

## Draw order as a tool

The first sibling draws on top, which makes stacking explicit: overlays first, then content, then backgrounds. The progress ring in `counter` was invisible until it was declared before its grey track. To reorder over time, key a `DrawRules` target (Rive docs), or swap visibility with a `Solo` (`activeComponentId`, `KeyFrameId`).

## Following the pointer (Rive docs, not built here)

A `ScriptedLayout` that fills the artboard writes pointer position into view model numbers, and binds move the scene (script `init` runs twice; the view model is nil the first time). Without a script, a `Joystick` scrubs two timelines from a dragged handle (`-1` is the first frame), or `ListenerAlignTarget` drags an object directly. Remember that a file with scripts must be `--publish`ed for the web.
