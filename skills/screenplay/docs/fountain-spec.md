# Fountain Syntax Specification

Complete reference for the Fountain screenplay markup language.
Official spec: https://fountain.io/syntax/

## Title Page

Optional. Must be the first thing in the document. Key:value pairs.

```fountain
Title: Big Fish
Credit: written by
Author: John August
Source: based on the novel by Daniel Wallace
Draft date: 1/20/2003
Contact:
    John August
    john@johnaugust.com
```

Values can be inline or indented on the next line (3+ spaces or a tab).
Recommended keys: Title, Credit, Author, Source, Draft date, Contact.
An implicit page break follows the title page.

## Scene Headings (Slug Lines)

Begin with INT, EXT, EST, INT./EXT, INT/EXT, or I/E (case insensitive).
Require a blank line before and after.

```fountain
INT. COFFEE SHOP - DAY

EXT. ROOFTOP - CONTINUOUS

INT./EXT. MOVING CAR - NIGHT
```

**Force any line as a scene heading** with a leading period:
```fountain
.UNDERWATER SEQUENCE
```

**Scene numbers** (for shooting scripts):
```fountain
INT. HOUSE - DAY #1#
INT. HOUSE - DAY #1A#
```

## Action

Any paragraph that doesn't meet criteria for another element.
Respects line breaks intentionally. Written in present tense.

```fountain
The men look at each other. Nobody moves.

Rain hammers the pavement. A dog barks in the distance.
```

**Force a line as action** (prevents uppercase interpretation as character):
```fountain
!BANG! The door flies open.
```

## Character

Must be entirely UPPERCASE with a blank line before it. No blank line after.
Must contain at least one alphabetical character.

```fountain
DIMITRIS
I used to know things.
```

Extensions in parentheses:
```fountain
MOM (V.O.)
Be careful out there.

COUNSELOR (O.S.)
When was the last time you slept?
```

**Force a character with mixed case** using @ prefix:
```fountain
@McCLANE
Yippee ki yay.
```

## Dialogue

Follows a Character or Parenthetical element immediately.

```fountain
DIMITRIS
I have nothing to say. And yet
here I am, saying it.
```

Line breaks within dialogue are respected.

## Parenthetical

Wrapped in parentheses, follows Character or Dialogue.

```fountain
DIMITRIS
(sotto voce)
This is fine. Everything is fine.
```

## Dual Dialogue

Add a caret ^ after the second character name:

```fountain
DIMITRIS
I'm leaving.

MARIA ^
You just got here.
```

## Lyrics

Prefix with tilde ~:
```fountain
~When the morning comes
~I'll be far away
```

## Transitions

Uppercase, end with TO:, blank lines before and after.

```fountain
CUT TO:

FADE TO:

SMASH CUT TO:
```

**Force any line as a transition** with leading >:
```fountain
> Burn to white.
```

## Centered Text

Bracket with > and <:
```fountain
>THE END<

>A film by Nick Athens<
```

## Emphasis

```fountain
*italics*
**bold**
***bold italics***
_underline_
```

Escape with backslash: `\*not italic\*`
Does not carry across line breaks.

## Page Breaks

Three or more equals signs on their own line:
```fountain
===
```

## Line Breaks

Carriage returns are treated as intentional. In dialogue, use two spaces on an empty line to preserve a blank line.

## Notes

Double brackets. Won't appear in formatted output.
```fountain
[[This scene might need to be cut.]]

He opens the door [[maybe too cliche?]] and steps inside.
```

## Boneyard

Wrap content to completely exclude from output:
```fountain
/* This entire scene was removed in draft 3.
INT. OLD LOCATION - DAY
All the content here is invisible. */
```

## Sections

Markdown-style headers. Organizational only, not printed.
```fountain
# Act One
## Opening Sequence
### Scene Group: The Setup
```

## Synopses

Prefix with =. Paired with sections or scenes. Not printed.
```fountain
# Act One
= The hero's ordinary world is established.

INT. OFFICE - DAY
= Dimitris arrives late to work again.
```
