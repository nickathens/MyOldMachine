# Where Rive fits this studio's work

Rive's pitch is interactive graphics for apps and games; the uses that matter here are narrower and practical. Each line says what to build and how it leaves the machine.

## Screens for film, TV and commercials

The strongest fit. Territory Studio, who design screen graphics for feature films, build film UI in Rive (their Unreal Fest 2024 talk with Rive: Hollywood features, automotive, tech products), and Rive markets "film and TV" as a use case: interfaces that can be filmed in camera because they react for real.

- **For comps:** build the phone, laptop or car screen as RML (`ui_screen` is the start: layout, data-driven lists, typing with a caret, a scripted scroll), render at the device's pixel size with `--size` (vectors, never upscaled), deliver ProRes 4444 with alpha or 422 HQ at the shot's frame rate. Timing is exact and repeatable: the same drag throws the same distance on every render, so a redo matches frame for frame. Clicks, drags and key presses land at set times from a timeline file.
- **For in-camera playback:** the same file as a web page (`rive_web.py page`) on the prop screen, driven live by touch or by data from an operator.
- Versions for different countries or products are rows in a table (`rive_versions.py`).

## Supers, lower thirds and name cards

`lower_third` plus a CSV is a whole package of supers as ProRes 4444 in one command. The plate hugs the text, so long names do not need a different template. Check each render against the studio's supers rules (legibility, clearance) before delivery; the postproduction skill's supers audit applies to the rendered frames.

## Live broadcast and streams

Rive files run live with data coming in: Games Done Quick's charity marathon runs its lower-third "omnibar" (over 100 live data points, staff overrides) as a Rive file in a React broadcast page. Community plugins put `.riv` files into OBS (aloisdeniel/rive_obs_source, MIT), TouchDesigner (medcelerate/TDRive) and disguise (medcelerate/RiveRenderStream). Not tested here.

## Logos and identity

- A logo sting from any SVG: `rive_svg.py logo.svg --project ... --reveal`, then refine timing in the RML. Deliver as video, or as a live web piece.
- The same mark as an interactive web element (hover, click, scroll-driven) for the studio site or a pitch: `rive_web.py page`.
- A raster logo goes through the logo-animate skill's vectoriser first.

## Music

Music visualisers for releases and social: `rive_audio.py` turns a track into per-frame curves (loudness, onsets, bands, a 12-band spectrum), `visualizer` shows how to bind them, and the render muxes the track back in. Also possible and not built yet: a character or shape rig whose poses follow the curves (Duolingo drives its characters' mouths from speech with Rive: a "visemes" state machine), or interactive music pieces on the web that react to a listener.

## Pitches, treatments and presentations

Treatments and decks go out here as live web pages. A Rive piece inside one is a small, sharp, interactive element (a product demo, a data build, an animated logo) that stays crisp at any size. `rive_web.py page --single-file` makes a self-contained piece; `--controls` lets a client play with the data in the meeting.

## Social and data

Animated numbers and charts from data (`counter`), in square or vertical sizes (render the same file at another `--size` after making its layout responsive), with versions from a table.

## Games and prototypes

The Godot skill pairs with RiveGD (maidopi-usagi/RiveGD, MIT, Godot 4), and Rive has official Unity and Unreal runtimes: game UI, HUDs and menus built here run there. For app prototypes, the `button` template is the pattern for every control: hover, press, toggle, keyboard and screen reader in one file.

## When not to use Rive

- Anything that alters camera footage (keys, comps, grades, cleanup, retimes): postproduction and colorgrade.
- Photoreal or 3D: blender (Rive can do 3D with WGSL scripts, as one community project showed with a GLB television, but it is a stunt).
- Long-form editing of video clips: video-editing, davinci-resolve.
- A motion graphic that must come from React components, or needs a video element inside it: remotion (mind its company licence).
- Generative art as code: algorithmic-art.
- Static diagrams: diagram.
