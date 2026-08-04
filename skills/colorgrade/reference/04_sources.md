# Sources, and what was actually verified

Split into three. What was read from a primary document. What was measured on
this machine. What is second hand and should be treated as a lead.

## Read from the primary document

**Blackmagic developer notes**, on disk at
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/`.
Both README files read line by line, not summarised.

- `DaVinciCTL/README.txt` L77 to 89: the transform signature carries `p_X` and
  `p_Y`, the pixel coordinates. This is the whole reason the object track is a
  DCTL and not a LUT.
- L91: `_tex2D` reads any pixel, so a DCTL can sample neighbours.
- L219 to 246: `DEFINE_UI_PARAMS` gives sliders, check boxes, combo boxes and a
  colour picker, so the delivered file carries live controls.
- L262 onward: alpha output exists since Resolve 19.1, but only through the
  ResolveFX DCTL plugin.
- Their shipped sample `AlphaCircularWindow.dctl` builds a soft edged circular
  alpha from `_hypotf` plus a smoothstep. Blackmagic wrote the lens case.
- `Scripting/README.txt`, all 1130 lines. CAN: `SetCDL`, `Graph.SetLUT`,
  `Graph.ApplyGradeFromDRX`, `AddClipMattesToMediaPool`, `ExportStills` in drx,
  `ExportLUT`. CANNOT: searched the whole file, zero hits for AddNode,
  InsertNode, CreateNode, Qualifier or Window. No node creation, no qualifier,
  no power window, no key wiring.

**Kodak**, VISION Color Print Film 2383/3383 data sheet, downloaded from
kodak.com. Higher D max in the upper scale giving improved black on projection;
toe areas producing more neutral highlights on projection; absorber dyes
controlling intragrain light scatter. These are the three things kodak2383 is
built from.

**Blackmagic Colorist Guide to DaVinci Resolve 20**, published free by
Blackmagic, downloaded from documents.blackmagicdesign.com. The ordering of
normalise, balance in group mode, then per clip work, comes from here.

**Sharma, Wu and Dalal 2005**, the CIEDE2000 supplementary test data. The dE2000
implementation is checked against their published table of 12 pairs in
`selftest.py` and agrees to 4 parts in 100,000. This is a real verification, not
a plausibility check.

**Licences**, every one read from the actual LICENSE file rather than from a
badge or a summary. numpy BSD-3, scipy BSD-3, pillow MIT-CMU, PySceneDetect
BSD-3, OpenCV Apache-2.0. All clean for paid client work.

Previously verified for the segmentation route and worth keeping on record:
facebookresearch/sam2 Apache 2.0; GroundingDINO Apache 2.0; Cutie MIT; XMem MIT;
BiRefNet MIT. NOT usable on a paid job: Tracking-Anything-with-DEVA is
CC BY-NC-SA 4.0 non commercial, RobustVideoMatting is GPL-3.0 copyleft,
MatAnyone is S-Lab 1.0 non commercial. SAM 3 permits commercial use but its
section 8 lets Meta change the terms at any time with immediate effect, so it is
not an irrevocable grant the way SAM 2's Apache licence is.

## Measured on this machine

Every number in `03_failures.md`. Plus:

- The DCTL harness compiles Metal at runtime through `MTLDevice.makeLibrary`,
  with no Xcode installed. A 2560 by 1440 frame runs in 0.94 to 1.70 ms.
- The harness is a LOOSER translator than Resolve's own. A DCTL that compiled
  in the harness was rejected by Resolve with "Error Processing DaVinci CTL".
  Compiling in the harness is necessary and not sufficient. The rule that made
  it pass: use only constructs that appear in Blackmagic's own 13 shipped
  sample .dctl files. Specifically, never pass `__TEXTURE__` into a helper
  `__DEVICE__` function, and never output alpha outside the ResolveFX plugin.
- The working route into Resolve was confirmed by screenshot on a Resolve
  Studio 21 machine: Effects, OpenFX, ResolveFX Color, DCTL onto the node, then
  the DCTL List dropdown. All 16 UI controls arrived with their compiled
  defaults. Dragging the .dctl from the LUT browser never works for a DCTL with
  UI parameters.
- SAM 2 runs on Apple Silicon MPS on this M4 Pro. A 2560 by 1440 encode takes
  125 ms on MPS against 240 ms on CPU, bit identical masks either way. One
  click at the lens centre scored 0.989 and held the drawn CG lens. Video
  propagation runs at 637 ms per frame. It silently skips its own hole filling
  step on Mac because that needs a CUDA extension, and prints so in passing.
  Its output is binary, which per failure 7 costs half the colour move at the
  rim, so it answers where and never how the edge should look.
- Grading and rendering a 12 second 2560 by 1440 clip end to end, including
  detection, measurement, two derivation passes, LUT baking, contact sheet and
  encode: 13.7 seconds wall clock, of which 6.3 is the render, 1.9 times real
  time.
- Analysing a 2 minute 45 second 1080p film, 21 shots: 5.4 seconds.

## Second hand, treat as a lead

- That external scripting from a terminal is Studio only since Resolve 19.1.
  Consistent across many reports and consistent with the free edition on this
  machine, but not executed here.
- That a .drx is the whole node tree including qualifiers and windows, with
  external resources stored as references. Forum and vendor blogs, not
  Blackmagic. No .drx exists on this machine to inspect.
- The skin tone line at 123 degrees being the plus I axis inherited from NTSC.
  The 116 to 126 range is attributed to Keith Jack's Video Demystified, which
  was not read directly.
