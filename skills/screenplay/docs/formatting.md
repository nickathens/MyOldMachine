# Industry Standard Screenplay Formatting

The rules that make a script look professional. Deviation signals amateur.

## The Basics

| Element | Font | Size | Paper |
|---------|------|------|-------|
| Everything | Courier or Courier Prime | 12pt | US Letter (8.5" x 11") or A4 |

**Why Courier?** Every character occupies the same horizontal space (monospaced). This makes the 1 page = 1 minute rule work. Variable-width fonts break this relationship.

## Margins

| Element | Left Margin | Right Margin |
|---------|-------------|--------------|
| Action | 1.5" | 1" |
| Character name | 3.7" | (right edge) |
| Dialogue | 2.5" | 2.5" |
| Parenthetical | 3.1" | 2.9" |
| Transition | (right-aligned) | 1" |
| Scene heading | 1.5" | 1" |
| Page number | (right-aligned) | 0.5" from top |

The left margin is wider to accommodate three-hole punch binding.

**Note:** When writing in Fountain format, you don't set margins. The formatter (screenplain/afterwriting) handles all of this automatically from your plain text.

## Spacing Rules

- **Single space** between character name, parenthetical, and dialogue (they're a unit)
- **Double space** (one blank line) between all other elements
- **Double space** between scenes (some scripts use triple)
- Page numbers in the top right corner, starting on page 2

## Scene Headings

Always structured as: **INT/EXT. LOCATION - TIME OF DAY**

```
INT. KITCHEN - NIGHT
EXT. PARK BENCH - DAY
INT./EXT. CAR - CONTINUOUS
```

Time options: DAY, NIGHT, DAWN, DUSK, CONTINUOUS, LATER, MOMENTS LATER, SAME

**CONTINUOUS** = the scene picks up immediately from the previous scene (same time, different location).
**LATER** = same location, time has passed.

## Character Introductions

First appearance of a character: name in ALL CAPS within action, followed by a brief, visual description.

```
DIMITRIS (30s), unshaven, wearing yesterday's hoodie like armor,
sits at the only occupied table in an otherwise empty cafe.
```

After introduction, the name appears normally in action (not capped).

## Extensions

After character name, in parentheses:
- **(V.O.)** = Voice Over (narrator, phone call where we only see one side)
- **(O.S.)** = Off Screen (character is in the scene but not visible)
- **(CONT'D)** = Continued (same character speaking after an action line interruption)

## Transitions

Right-aligned, in caps. Use sparingly in spec scripts.

```
                                                    CUT TO:
                                                    DISSOLVE TO:
                                                    SMASH CUT TO:
                                                    FADE TO BLACK.
```

Modern spec scripts use very few transitions. The edit is implied by the scene change. Only use CUT TO: for jarring or deliberate cuts. FADE IN: opens the script, FADE OUT. closes it.

## Page Count Guidelines

| Format | Pages | Runtime |
|--------|-------|---------|
| Short film | 5-15 | 5-15 min |
| Half-hour TV | 22-32 | 22-32 min |
| Hour TV | 45-65 | 45-65 min |
| Feature film | 90-120 | 90-120 min |

Going over 120 pages is a red flag for features. Going over 15 pages is a red flag for shorts.

## What NOT To Do

- **Don't direct on the page.** No camera angles (CLOSE ON, PAN TO, ANGLE ON) unless absolutely necessary for story. The director decides
- **Don't number scenes** in a spec script. Scene numbers are for shooting scripts only
- **Don't use (MORE)/(CONT'D)** manually. The formatter handles dialogue continuation across page breaks
- **Don't use bold/italic/underline** excessively. One per script is fine for extreme emphasis. More than that and you're yelling
- **Don't use WE SEE or WE HEAR.** Of course we see it. It's a movie
- **Don't use CUT TO: between every scene.** It's implied
- **Don't write (beat).** Describe the pause as action instead: "He stares at his hands."

## Non-English / European Considerations

- A4 is standard outside North America. Use `--a4` flag when exporting with afterwriting
- Many European festivals and funding bodies accept scripts in the original language
- Non-English scripts follow the same Fountain formatting rules; the language only changes the words
- Courier supports many non-Latin alphabets, but Courier Prime may need a script-specific fallback font
- For competition submissions: always check the current call's exact format requirements (PDF settings, page count limits, language)
