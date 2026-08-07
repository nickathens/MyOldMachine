# Matching a film to a series that is already approved

The headline mistake, because it is the one that gets made: **matching the recipe
instead of the result.**

Three films in one corporate series were given the same look values. The third
came back as only adequate. Measuring what each grade actually added to its own
footage showed the grades were nearly identical: same lift in colour, same push
on skin, same cool shadows. The films looked different because the FOOTAGE was
different. One arrived colourful and dark, a tungsten interior. The next arrived
pale and neutral, a daylight office. The same recipe, a flatter result.

**Carry the family's directions. Re-derive every magnitude from the new film's
own measurements.**

`cgseries.py landing` reports where a film lands. `cgseries.py compare` puts two
films side by side on those numbers. Close the gaps that should be closed and
leave alone the ones that are the lighting: a tungsten interior and a daylight
office should not match on brightness, and forcing them to is a worse error than
the mismatch.

---

## The contrast pivot has to sit where the film's pixels are

The engine pivots contrast at Cineon mid grey, log 0.4573. A film whose own mid
sits well below that gets far less tonal work out of an identical setting. On one
film the mid sat at 0.442 against a sibling's 0.389, and the identical contrast
setting did **four and a half times less** work. It read flat, and it read as the
footage's fault.

**But the rule must be re-derived, not copied.** A film with two worlds, a dark
half and a bright half, has its own median sitting in the VALLEY between them.
Measured on such a film: dark world at log 0.245, bright world at 0.534, and only
2.8 per cent of its pixels near the 0.383 median between them. Pivoting at the
median drove the bright half from 162 to 179 of 255 and clipped 6.8 per cent of
it. Pivoting on the bright world's own mid held it at 163 and spent the whole
contrast deepening the dark half.

`cgseries.py pivot` reports whether the film is bimodal and refuses to give one
answer when it is. Two details in how it measures:

* the worlds are split by **frame**, not by pixel value. Splitting pixels puts
  every bright pixel of the dark world into the bright bucket and drags its
  middle up. A world is a stretch of the film, not a range of values.
* the peak of a lobe is not where that world sits. A bright world with a long
  tail downward peaks well above its own middle. Split at the valley, then take
  each world's median.

A full frame title card is not the film. If a piece opens or closes on one, cut
it out before measuring.

---

## Split tone: the same argument, in colour

A family shadow tint that pushes blue is right for the film that needed cooling
and wrong for the next one. One film in the series arrived with a shadow lean of
−11.05 against its siblings' −1.72 and −2.21, because it was lit blue. Adding the
family tint on top took it to −17. Cutting it to a third was the same rule as
moving the pivot: match where the film LANDS, not the recipe.

---

## Read brand colours off flat artwork, never off a mask

Masks failed three times running on this material.

* An HSV hue band "skin" mask selected 46 per cent of a film. Rendered in
  magenta it showed wood and walls. It is a mask for **warm**, not for skin.
  Rebuilt in Lab with an r > g > b ordering test.
* A "shirt" anchor, about to be used to decide which of two colour states was
  correct, turned out to select the entire blurred background.
* A hue band mask claimed two thirds of a film was off brand. It was measuring
  denim and window glass.

**Render the mask in magenta and look at it.** That settled five separate
disputes on one series. `cgseries.py mask` does it. Do it before any claim that
rests on a mask, automatically, not as a last resort.

**Then stop using masks for this.** A title card is flat, known artwork, and the
same card usually opens every film in the series, so it is the one honest common
reference between two pieces. `cgseries.py primaries` reads the colours there,
at full resolution: reading a card at a working scale mixes a small brand mark
with the background it sits on and rotates its hue toward that background, which
is the same class of error as a bad mask.

Gate on Lab chroma, not HSV saturation. A near white with a faint cast passes a
saturation test and is not a brand colour.

---

## A synthetic probe must round trip before it measures anything

A synthetic Lab probe built at a brand's real chroma can fall **outside**
Rec.709. Seven of seven test teals had a negative linear channel, and the clip
rotated their hue 6.9 degrees before any look ran. That reported as 18.5 degrees
of brand drift which did not exist.

`cgseries._assert_in_gamut` refuses a probe that will not survive the round trip.
Assert it, do not assume it.

---

## Two hue conventions in one call

`hue_shifts` gates in HSV and rotates in Lab. Those are different numbers for the
same colour and they differ by a different amount for every colour:

| colour | HSV hue | Lab hue | apart |
|---|---|---|---|
| brand teal | 189.20 | −151.21 | 19.59 |
| brand lime | 88.82 | 117.99 | 29.17 |
| skin, lit | 15.18 | 52.62 | 37.43 |
| sky blue | 227.08 | −76.28 | 56.65 |
| warm wood | 16.58 | 61.56 | 44.98 |

A centre measured in Lab and gated in HSV selects a colour that is not in the
picture and the shift silently does nothing. On a real job four holds were inert
and the graded film printed numbers identical to no holds at all, which very
nearly read as "the holds do not help" for entirely the wrong reason.

Each entry now takes `"space": "hsv"` (default, what the library looks were
authored in) or `"lab"`. Gate a measured centre in the space it was measured in.
An unknown space raises rather than guessing.

---

## A hold cannot fix drift that contrast caused

Worth knowing before reaching for hue holds at all. Per-channel log contrast
changes R:G:B ratios, so the resulting rotation depends on lightness and chroma
within one hue band. On one film the same teal band held glass discs at L69
rotating one way and the logo mark at L64 rotating the other, and no single
rotation can undo both.

Turning contrast off took teal drift from 3.55 to 1.99 degrees. Crosstalk, gamut
compression, tints and saturation each moved it by under 0.4 degrees. So on that
film every hold made things worse and none was shipped; the brand still moved
less than on the approved sibling, by being gentler rather than by correcting.

---

## Sample the pixels, do not hold the frames

A look sweep built on full frames reached 10 GB and drove the machine into swap.
Rebuilt on a sampled pixel population it gave the same numbers with forty times
less memory. `cgseries.sample_pixels` does this. Do it first, not after the
machine complains.
