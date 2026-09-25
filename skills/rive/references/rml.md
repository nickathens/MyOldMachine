# RML: how it works and what bites

RML is Rive's object model written as XML: every element is a core type, every attribute one of its properties, and nesting stands in for the references between objects. One project can hold several `.rml` files; they compile as one document. The authoritative, version-matched text is `rive docs <topic>` (`format`, `skeleton`, `workflow`, `gotchas`, `drawing`, `transforms`, `text`, `layout`, `data`, `state-machines`, `easing`, `rigging`, `assets`, `semantics`, `focus`, `cursor`, `publishing`, `push`, `project/rive-yaml`, `luau/protocols`, `luau/api/...`). This page is the working summary, plus what was found by building the six templates. It paraphrases rather than copies the CLI's docs, which ship without a licence.

## The shape of a file

```xml
<Rive version="1" kind="fragment">
    <Artboard defaultStateMachineId="0:90" styleId="0:3" width="1920" height="1080" name="Main" id="0:2">
        <LayoutComponentStyle name="Artboard Style" id="0:3"/>
        <!-- shapes, text, layout boxes; the FIRST one declared draws on top -->
        <LinearAnimation fps="60" duration="120" name="Intro" id="0:80"> ... </LinearAnimation>
        <StateMachine name="Main Machine" id="0:90">
            <StateMachineLayer name="Play" id="0:91">
                <AnyState x="400" y="0"/>
                <ExitState x="560" y="0"/>
                <EntryState x="0" y="0"><StateTransition stateToId="0:92"/></EntryState>
                <AnimationState x="200" y="0" animationId="0:80" id="0:92"/>
            </StateMachineLayer>
        </StateMachine>
    </Artboard>
    <!-- root elements: view models, converters, enums, assets -->
    <FontAsset file="SpaceGrotesk-Variable.ttf" name="Space Grotesk" id="0:50"/>
</Rive>
```

- **Ids** are `client:object` pairs (`0:12`), one namespace for the whole document, no leading zeros, `0:0` reserved. Only give ids to what something references. Two examples merged together usually clash; renumber one.
- **Root elements** (`ViewModel`, `DataConverter*`, `DataEnumCustom`, `FontAsset`, `ImageAsset`, `AudioAsset`, `ComponentAsset`) sit directly under `<Rive>`, never inside an artboard. No `<Backboard>`: default artboard and publish options live in `rive.yaml` (`main:`).
- **Nesting sets references.** A `KeyFrame` inside a `KeyedProperty` belongs to it; a `LayoutComponentStyle` nested in a box still needs the box's `styleId`. When unsure which way a reference runs, `rive docs format` has both tables.
- Give every artboard and every state an `x`/`y`: the CLI ignores them, the editor stacks everything at the origin without them (`inspect` warns `artboards-overlap` / `states-overlap`).

## Units and values

| Thing | Unit |
|---|---|
| Colours | ARGB hex, no `#`: `FFC9A84C` is opaque gold, `80FF0000` half-transparent red |
| Rotation | radians (a full turn is `6.2831855`); `rotation-looks-like-degrees` warns past a turn |
| `LinearAnimation.duration` | frames, at `fps` (default 60) |
| `StateTransition.duration`, `exitTime` | milliseconds, unless `durationIsPercentage` / `exitTimeIsPercetange` (misspelled in the format; the correct spelling is rejected) |
| `DataConverterInterpolator.duration` | seconds |
| Origins (`originX`/`originY`) | 0..1 of the shape; the artboard's origin also moves its coordinate space, so leave it at 0 |
| `childOrder`, `order` | a fraction string `"3/4"`; a bare `"1"` is silently invalid |
| Enums | names are checked (`layoutWidthScaleType="fill"`), integers are not; prefer names |
| Booleans | `"true"` / `"false"` |

## Property keys you will key and bind

Keyframes and binds name properties by number. These were read from `rive schema <Type> --animatable/--bindable` on CLI 1.1.1; look up anything else rather than guessing.

| Property | Key | Property | Key |
|---|---|---|---|
| x, y | 13, 14 | rotation | 15 |
| scaleX, scaleY | 16, 17 | opacity | 18 |
| width, height (Rectangle, Ellipse, Triangle, Polygon, Star) | 20, 21 | Star innerRadius | 127 |
| LayoutComponent width, height | 7, 8 | Text width, height | 285, 286 |
| SolidColor colorValue | 37 | GradientStop colorValue, position | 38, 39 |
| Stroke thickness | 47 | Feather strength, offsetX, offsetY | 749, 750, 751 |
| TrimPath start, end, offset | 114, 115, 116 | Dash length, DashPath offset | 692, 690 |
| TextValueRun text | 268 | TextStylePaint fontSize, lineHeight, letterSpacing | 274, 370, 390 |
| TextStyleAxis axisValue | 288 | TextModifierRange modifyFrom, modifyTo | 327, 336 |
| TextModifierRange falloffFrom, falloffTo, offset | 317, 318, 319 | Solo activeComponentId | 296 |
| Image assetId (bind an image) | 206 | ArtboardComponentList listSource | 800 |
| NestedRemapAnimation time (0..1 of its length) | 202 | FollowPathConstraint distance | 363 |
| Joystick x, y (-1..1) | 299, 300 | ScriptInputNumber value | 243 |
| ViewModelInstance value: string, number, boolean, colour | 561, 575, 593, 555 | FormulaTokenValue (bind a token) | 777 |

Bindable adapters inside state machines and listeners (the element name picks the type; several share a key): `BindablePropertyBoolean` 634, `String` 635, `Number` 636, `Enum` 637, `Color` 638, `Integer` and `Trigger` 686, `Asset`/`Artboard`/`ViewModel` 823, `List` 835.

Keyframe elements must match the property's type: `KeyFrameDouble` (numbers), `KeyFrameColor`, `KeyFrameBool`, `KeyFrameUint` (integers and enums), `KeyFrameString` (text), `KeyFrameId` (references such as a Solo's active child), `KeyFrameCallback`. A mismatch builds, inspects clean and never writes.

## Drawing

- A `Shape` holds position and transform; its geometry child (`Rectangle`, `Ellipse`, `Triangle`, `Polygon`, `Star`, `PointsPath`) holds size; a `Fill` or `Stroke` needs a `SolidColor` or gradient inside it or it draws nothing.
- **Draw order:** the first sibling draws on top. Inside one shape, the last paint draws on top (put a glow stroke first, the crisp stroke after).
- A Rive `Ellipse` path starts at 12 o'clock and runs clockwise, so a `TrimPath` on it fills from the top with no rotation (an SVG circle starts at 3 o'clock). Measured in the `counter` template.
- Paths: `StraightVertex` (optional `radius`), `CubicMirroredVertex` (`rotation`, `distance`), `CubicAsymmetricVertex` (`rotation`, `inDistance`, `outDistance`), `CubicDetachedVertex` (`inRotation`, `inDistance`, `outRotation`, `outDistance`). A handle sits at the vertex plus (cos r, sin r) times its distance; the mirrored and asymmetric `rotation` is the OUT handle, the in handle is opposite. Checked by `rive_svg.py` against Chromium. Close a path with `isClosed="true"`.
- Effects: `TrimPath` (draw-on, progress rings), `DashPath`/`Dash`, `Feather` on a stroke (a blur; `inner`, `offsetX`/`offsetY`), `ClippingShape` (mask by another shape; keep the mask shape unhidden, give it no paint), `GroupEffect` + `TargetEffect` (one effect shared by many strokes), `NSlicer` (9-slice images), blend modes by name on shapes (`screen`, `multiply`...; numeric only on a `Fill`/`Stroke`).
- **Feather inside a Fill draws nothing** at any strength. A soft filled glow is a `RadialGradient` to a transparent stop; a soft drop shadow is a thick feathered stroke with `offsetY` on a slightly smaller shape declared after (under) the card (measured, `references/motion_recipes.md`).

## Layout

- A `LayoutComponent` is a flexbox box: its `LayoutComponentStyle` is **nested and named by `styleId`**, or none of it applies. Every artboard needs its own style too (the CLI renders without one, the editor then cannot lay it out and cannot add one later).
- Sizing per axis: `fixed` (its `width`/`height`), `fill` (a weight sharing the leftover space, not 100%), `hug` (fits its children). `fractionalWidth` weights live on the component or a participant, not the style.
- Padding, margin, gap, border and position insets need `*UnitsValue="points"` next to them or they are silently ignored (`rive_check.py` lint `units-undefined`).
- `layoutAlignmentType` is one property for both axes (`topLeft`...`bottomRight`, `center`, `spaceBetweenStart|Center|End`). Direction with `flexDirectionValue`; `flexWrapValue`; `layoutTypeValue="grid"` with `GridTrack` children on the component.
- A `Shape`, `Text` or `Image` inside a `fixed` or `fill` box does not join the flow and **is stretched to the box**: wrap composite art in a `Node` (keeps size and position), give a single item a `LayoutParticipant` (then it is a flex item), or put it in its own component artboard.
- Measured while building the templates: a `LayoutComponent` inside a plain `Node` was still placed by the artboard's layout, off where the Node put it, and its listener missed; a `Text` inside a `Solo` inside a fixed box ignored its `y`. Keep interactive boxes as direct layout children and switch labels by keying the text instead (the `button` template).
- Pin a box with `positionTypeValue="absolute"` and the insets you want; all four insets at 0 stretch it over its parent without joining the flow (an overlay plate, the `button` template).
- The artboard's own `Fill` ignores corner radius; round a card with a filled `LayoutComponent` inside (`ui_screen`'s rows).
- `x`/`y`/scale/rotation/opacity on a layout box offset it from where the layout put it, so keying them never reflows neighbours. `ComponentOrigin` sets its pivot.
- Scrolling for real: viewport (`fixed`, `clip="true"`) around content (`hug`) with a `ScrollConstraint` and an `ElasticScrollPhysics` (default friction 8 feels heavy; about 2.5 feels like a phone). For a rendered, deterministic scroll, key the content's `y` inside a `clip="true"` viewport (`ui_screen`).
- Check responsiveness early: `rive_check.py --sizes 390x844,1280x720`.

## Text

- Three objects: a `Text` (position, `sizingValue`, `alignValue`, `overflowValue`, origin), `TextStylePaint` (font, size, `lineHeight`, `letterSpacing`, a `Fill`), `TextValueRun` (the characters, `styleId`). Missing `styleId` or style `Fill` renders nothing; a wrong id fails the build.
- Fonts: a `FontAsset` with `file=`; no font means no text. Set `familyName` and `styleName` on each style or the editor's menus show `-`. A variable font renders its default instance unless a `TextStyleAxis` (wght = `2003265652`) says otherwise. `TextStyleFeature` switches OpenType features (`tnum` = `1953396077` keeps numbers from jumping in a counter).
- `alignValue` does nothing under `autoWidth`; centre a run on a point with `originX="0.5"`.
- `TextStyleBackground` inside a style paints a box that hugs the glyphs (a highlighter).
- Per-glyph motion: `TextModifierGroup` (the change; each property needs its `modify*` flag, and `invertOpacity="true"` when opacity is animated, or the whole text vanishes) with a `TextModifierRange` inside (which glyphs; units characters/words/lines). A reveal keys `modifyFrom` from `-0.25` to `1` and `falloffFrom` from `0` to `1.25` together, so every glyph starts fully hidden and each eases in (`lower_third`).
- Scripts cannot draw text; build text in RML.

## Data

- A `ViewModel` (PascalCase) with properties (camelCase; not Luau keywords like `type`, no leading digit), and a `ViewModelInstance exports="true"` holding values. The artboard names it with `viewModelId` (and `viewModelInstanceId` for the editor preview).
- Bind with `<DataBindContext sourcePathIds="VM-PROP[-PROP]" propertyKey="..." [converterId="..."]/>` nested in the target. Paths are absolute ids; relative (`nameBased`) binds cannot be built by the CLI and do nothing.
- **Binds only run while the default state machine runs.** An artboard with no `defaultStateMachineId` shows authored literals, delivers no pointer input and still plays its first timeline. Component artboards (list rows, nested artboards) need their own.
- Lists: `ViewModelPropertyList` + `ViewModelInstanceList` of `ViewModelInstanceListItem`s + an `ArtboardComponentList` bound with key 800. The row artboard is found by matching `viewModelId`; mark it `isComponent="true"`, add a `ComponentAsset`, make its root `fixed` on both axes. `ViewModelPropertySymbolListIndex symbolTypeValue="itemIndex"` gives each row its index.
- Converters (root elements, chained with `DataConverterGroup`): `RangeMapper` (remap and clamp; also how to add a constant), `ToString` (`decimals` needs `round="true"`), `Formula` (tokens; operation numbers 0 `+` 1 `-` 2 `*` 3 `/`...; a `FormulaTokenValue` can bind another property, key 777), `Interpolator` (eases CHANGES over `duration` seconds; the first bound value arrives instantly), `BooleanNegate`, `ListToLength`, `NumberToList`, `Rounder`, `StringPad`, `Trigger`, `ScriptedDataConverter`.
- Global view models (`viewModelType="global"`) are design tokens every artboard can read; `--data` cannot set them.
- Stateful components: a `NestedArtboard isStateful="true"` with its own `ViewModelInstance` child gives each placement its own data.

## State machines

- Layers run at the same time; give each independent behaviour its own layer (the `button` has Hover, Press and Toggle). Two layers keying the same property fight; the later layer wins.
- Every layer needs `AnyState`, `ExitState` and `EntryState` with a transition out, or it never starts. States take no `name`.
- Drive states with view model properties, not the deprecated `StateMachineBool/Number/Trigger` inputs: listeners write a property (`ListenerViewModelChange` with a `BindableProperty*` whose bind has `direction="true"`), transitions read it (`TransitionViewModelCondition` with a `TransitionPropertyViewModelComparator` and a `TransitionValue*Comparator`).
- A toggle writes the negation of the current value: `fromViewModelProperty="true"` and `fromDataBindId` naming a read context that carries the `DataConverterBooleanNegate`, plus a write context. Without the read context every click writes the same value and the control latches. Author both transitions.
- One-key animations per state plus a transition `duration` is a cross-fade; `duration="0"` snaps.
- A layer with no conditions plays forever: the recipe for idle loops (the timeline needs `loopValue="loop"`; the default is one shot).
- Listeners: `StateMachineListenerSingle` (`listenerTypeValue` enter, exit, down, up, move, click, drag...) for pointers; the general `StateMachineListener` with a `ListenerInputTypeKeyboard`/`Gamepad`/`Semantic` child for the rest. Every listener under the pointer fires; a transparent fill still hits; `isTargetOpaque` blocks.
- Keys reach only a focused node: give it a `FocusData` child and focus it from the entry state with `FocusActionTarget` (the CLI previewer focuses the first one on its own, which hides the bug). `KeyboardInput keyPhase="1"` is press; `0` matches nothing and `7` fires twice.
- Events: a plain `Event` is reported to the host; `AudioEvent` plays an `AudioAsset` (WAV, MP3, FLAC); `OpenUrlEvent` opens a link.
- Accessibility: a `SemanticData` child (`role`, `label`, traits) describes a node; `rive <dir> --semantics=-` prints the tree.

## Silent failures and how to catch them

| Failure | Detect |
|---|---|
| no default state machine: binds and pointers dead, first timeline still plays | `inspect` `no-default-state-machine`; `rive_render.py` warns |
| layout style nested but not linked by `styleId` | `rive_check.py` (jq check in `rive docs workflow`) |
| padding/gap/inset without `*UnitsValue` | `rive_check.py` lint `units-undefined` |
| keyframe type does not match the property | nothing reports it; render and look |
| `cubic`/`elastic` key with no interpolator child | lint `curve-missing` |
| ease on the last key instead of the first | render: the motion snaps |
| bind on the wrong object or property (a Shape has no width; key 20 is width, not height) | `rive_check.py --probe-binds` |
| dangling bind path, bind in a view model the artboard does not use | `inspect` problems catches the first, `--probe-binds` both |
| number bound straight to text | `inspect` `incompatible-bind-types` |
| text style with no `familyName`/`styleName` | lint `text-style-unlabelled` |
| font file missing, or taken from the system | build error / lint `system-font-embedded` |
| variable font at its default (light) instance | `rive_fonts.py info` |
| `TextModifierGroup` values without their flags, or opacity without `invertOpacity` | lint `modifier-flags-zero`; render |
| `Feather` in a `Fill` | lint `feather-in-fill` |
| hidden clipping source, empty mask | render |
| draw order backwards | render (the first sibling is on top) |
| toggle that works once, or a control that does nothing | `rive_check.py --interaction click@X,Y` |
| keyboard listener dead: nothing focused, or phase 0/7 | `--key` in a capture; lint `key-phase-*` |
| `ScriptInput` name matching no `Input<>` field | lint `script-input-unmatched` |
| a module required before it is declared | only the capture's console shows `require could not find` |
| `NestedSimpleAnimation` left paused (`isPlaying` defaults false) | lint `nested-animation-paused` |
| unsigned scripts on the web | `rive_web.py verify`; build with `--publish` |
| rest pose captured as frame 0 | `rive_render.py` always advances first |
| blank captures on Linux Mesa | `rive_render.py` / `rive_check.py` refuse single-colour frames |

## Scripts (Luau) in one screen

Use a script for what is computed each frame (particles, physics, generative drawing, a data converter, a custom transition condition, a path effect); build everything with a fixed structure in RML. Protocols: `Layout` (`ScriptedLayout`, gets a size), `Node` (`ScriptedDrawable`), `PathEffect`, `Converter` (needs `reverseConvert`), `ListenerAction`, `TransitionCondition`, `Tests` (a factory returning the test function, `return function(): Tests return function(test: Tester) ... end end`; a bare `function setup(test)` is skipped as "no Tests scripts found"; `rive <dir> --test` exits 6 on a failing case and `rive_check.py` runs it whenever the project has Luau). Type checking is strict and fails the build: annotate every parameter. A misspelled hook is silently ignored. `init` runs twice and `context:viewModel()` is nil the first time. A script reaches data through `context:viewModel()`, blobs by name (`context:blob('levels')` returns a buffer: a way to carry data such as an audio envelope), images, audio playback (no analysis), and WGSL shaders through a GPU canvas. `ScriptInput*` children feed `Input<>` fields by name, unchecked. `--verify` type-checks but does not run a script: capture it. **A file with scripts must be built with `--publish` to play on the web.** The full API is `rive docs luau/api/...`.
