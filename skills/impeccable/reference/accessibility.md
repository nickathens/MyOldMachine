# Accessibility

Focus states, keyboard support, ARIA, forms, screen readers, hit areas, motion and zoom.

Folded in from [better-accessibility](https://github.com/jakubkrehel/skills) (MIT, commit `a673333`). Attribution, the licence text, and the list of what changed live in `accessibility/NOTICE.md`.

Accessibility is not a compliance checkbox bolted on at the end; it is the floor for interface craft. Most of it is free if you use the platform: native elements ship with keyboard support, real labels announce themselves, and a visible focus ring is one CSS rule. Apply these principles when building or reviewing UI code, and match the project's existing styling system (Tailwind vs. plain CSS vs. CSS-in-JS) when applying fixes.

When reviewing, walk the interface as a keyboard-only user first (every flow must complete without a mouse), then as a screen-reader user: does each control announce a name, a role, and its state? When unsure, prefer the platform default over a custom rebuild, and remove ARIA rather than add it.

## Deep dives

| Document | When to read |
| --- | --- |
| [Focus & Keyboard](accessibility/focus-and-keyboard.md) | Focus rings, skip links, tabindex, focus trapping, APG keyboard patterns |
| [Semantics & ARIA](accessibility/semantics-and-aria.md) | Native elements first, button vs link, landmarks, accessible names, disabled states |
| [Forms](accessibility/forms.md) | Labels, autocomplete, error messaging, input types |
| [Screen Readers](accessibility/screen-readers.md) | Visually hidden content, live regions, toasts, alt text, SVG |
| [Hit Areas](accessibility/hit-areas.md) | Target sizes, expanding hit areas, collision rules |
| [Motion & Zoom](accessibility/motion-and-zoom.md) | `prefers-reduced-motion`, autoplay and timed UI, 200% zoom, reflow, rem vs px |

## Where this sits against the other reference docs

Three places where the older docs in this folder are looser than this one. This file wins.

**Focus rings.** `interaction-design.md` shows `button:focus { outline: none }` paired with a `:focus-visible` ring in `var(--color-accent)`. That is legal, the replacement is right there, but it is the second choice. Prefer the browser's own indicator and add only `outline-offset`, because it adapts to the platform and to forced-colors mode without the author predicting every background. If the design demands a custom ring, verify the whole perimeter against every color it crosses. See `accessibility/focus-and-keyboard.md`.

**Validation timing.** `interaction-design.md` says validate on blur. Keep that if the design wants it, but on its own it is not enough: submit must still run the full validation, submit must stay enabled until the request starts, and on failure focus has to move to the first invalid field. Blur validation is a courtesy; the submit path is the one keyboard and screen-reader users depend on. See `accessibility/forms.md`.

**Hit areas.** `interaction-design.md` lists touch targets under 44x44px as a flat failure. The conformance floor is WCAG 2.5.8 at 24x24 CSS pixels, and it carries spacing, inline, equivalent-control, user-agent and essential exceptions. 44px is the recommendation, not the rule. Report an undersized target against the exception it actually fails, not against 44. See `accessibility/hit-areas.md`.

Two things this file leans on and does not carry: contrast ratios and palette remediation live in `color-and-contrast.md`, and the 16px mobile input rule that stops iOS zoom lives in `typography.md`. Impeccable has no RTL or bidirectional layout coverage in any document, so treat that as unwritten rather than handled.

## Core Principles

### 1. Native Elements First

The first rule of ARIA: don't use ARIA when a native element exists. `<button>` for actions, `<a href>` for navigation (it must support Cmd/Ctrl/middle-click), never `<div onClick>`. No ARIA is better than bad ARIA.

### 2. Visible Focus Rings

Style `:focus-visible`, not bare `:focus`, so keyboard users get a ring and mouse users usually don't. Prefer the browser's unmodified focus indicator. If the design needs a custom ring, use a project focus token or another explicit color and verify the complete indicator against every adjacent color it crosses; `currentColor` is acceptable only after the same check. Use at least a `2px` solid perimeter or an equivalent visible area. Never use `outline: none` without a verified replacement, and preserve system colors in forced-colors mode.

### 3. Full Keyboard Support

Every pointer interaction needs a keyboard path, following the ARIA APG patterns: Escape closes overlays, arrow keys move within composite widgets (tabs, menus, listboxes), Tab moves between widgets, Enter and Space activate. Only `tabindex="0"` (join the natural tab order) and `tabindex="-1"` (programmatic focus), never positive values, which break the natural order. Composite widgets use roving tabindex: the active item is `0`, all others `-1`.

### 4. Trap and Restore Focus

Modals set `inert` on the background content, move focus inside on open, and return focus to the trigger on close. Add `overscroll-behavior: contain` so background content doesn't scroll.

### 5. Minimum Hit Area

WCAG 2.5.8's Level AA baseline is a 24×24 CSS-pixel target or one of its defined spacing, equivalent-control, inline, user-agent, or essential exceptions. For easier activation, aim for 44×44px in touch contexts and 40×40px in desktop interfaces when density permits. Extend with a pseudo-element if the visible element should stay smaller. Never let extended hit areas overlap.

### 6. Label and Type Every Control

Every input gets a `<label for>` or wrapping `<label>`; a placeholder is never a label, and label and control share one hit target: no dead zones between a checkbox and its text. Add `autocomplete` with a meaningful `name`, and the correct `type` and `inputmode` for the keyboard. Never block paste; users paste passwords and one-time codes.

### 7. Errors That Announce

Keep submit enabled until the request starts, then disable with a spinner while keeping the original label. Validate on submit: mark failing fields with `aria-invalid="true"`, point `aria-describedby` at the inline error text, and focus the first invalid field. Use native `disabled` when a native control is genuinely unavailable. Use `aria-disabled="true"` only when retaining focusability or discoverability is intentional; then block pointer, keyboard, and form behavior in code and style the state explicitly.

### 8. Accessible Names Everywhere

Icon-only buttons need a descriptive `aria-label`. Visible label text must appear in the accessible name. Decorative elements get `aria-hidden="true"`, never on a focusable element.

### 9. Don't Rely on Color Alone

Status needs a redundant cue: icon, text, or underline alongside the color. Determine which WCAG contrast requirement applies from the content and state, then use `color-and-contrast.md` to check the rendered foreground/background pair. When contrast fails, report the pair and requirement it misses; do not change the project's colors unless asked.

### 10. Honor prefers-reduced-motion

Wrap motion in `@media (prefers-reduced-motion: no-preference)` so it is opt-in. Under reduced motion, replace slides and scales with opacity crossfades; kill parallax and autoplay entirely. Independent of the preference: autoplaying media needs a visible pause control, and toasts carrying actions or errors stay until dismissed.

### 11. Announce Dynamic Content

Use `aria-describedby` for field-specific validation, a polite live region (`role="status"`) for non-urgent updates not tied to a control such as toasts or result counts, and `role="alert"` only for urgent errors not tied to a control. For reliable repeated polite announcements, render a stable empty region before updating its text; dynamically inserted alerts have different support and must be tested with the target screen readers.

### 12. Alt Text by Purpose

Decorative images get `alt=""`, informative images describe the meaning, functional images describe the action: a search icon button is `alt="Search"`, not `alt="magnifying glass"`.

### 13. Structure Is Navigation

Use headings that describe their sections and form a coherent outline; one page-level `<h1>` and properly nested levels are the recommended default, not standalone WCAG pass/fail rules. Expose one visible primary `<main>` landmark. When repeated navigation or chrome precedes it, make a "Skip to content" link the first focusable element. Anchored headings get `scroll-margin-top`.

### 14. Survive Zoom and Text Resize

The page must work at 200% zoom and reflow at 320px width without horizontal scrolling. Use `min-height` instead of fixed `height` on text containers, prefer `rem` breakpoints where they fit the codebase's conventions, and never use `user-scalable=no` or `maximum-scale=1`.

## Common Mistakes

| Mistake | Fix |
| --- | --- |
| `outline: none` to remove the focus ring | Style `:focus-visible` instead; mouse clicks won't show it |
| Custom focus color assumed to work everywhere | Verify the full indicator against every adjacent color and in forced-colors mode |
| `<div onClick>` for a button or link | `<button>` for actions, `<a href>` for navigation |
| Placeholder used as the only label | Add a visible `<label for>`; placeholders disappear on input |
| Positive `tabindex` to fix focus order | Fix the DOM order; only use `0` and `-1` |
| Repeated polite update inconsistently announced | Keep a stable empty status region and update its text; test the target screen readers |
| `assertive` live region for a routine toast | Use `polite`; reserve `assertive` for errors |
| `aria-hidden="true"` on a focusable element | Remove it or make the element non-focusable |
| Functional icon alt describes the picture | Describe the action: `alt="Search"`, not `alt="magnifying glass"` |
| `maximum-scale=1` to stop iOS input zoom | 16px input font on mobile (see `typography.md`); never block zoom |
| Submit disabled until the form is valid | Keep it enabled; validate on submit and focus the first error |

## Review Output Format

Use this format when the user asks for a standalone accessibility review. When the `audit` command runs the wider quality pass, feed it the findings below and let its report structure take precedence.

Present the standalone review in two parts.

### Findings

Group all confirmed findings by principle. Use a markdown table with **Severity**, **Location**, **Before**, **After**, and **Why** columns. Never use separate "Before:" / "After:" lines.

- **Severity**: `HIGH` prevents a task, hides content from assistive technology, or creates a systemic accessibility failure; `MEDIUM` makes an interaction meaningfully harder; `LOW` is isolated polish.
- **Location**: cite `path/to/file:line`. If the artifact has no source files, cite the exact screen and component instead.
- **Before / After**: show the current implementation and an actionable replacement.
- **Why**: name the violated principle and its user impact.

Consolidate a repeated systemic issue into one row and list every affected location. Omit principles with no findings.

### Example

#### Accessible names everywhere
| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH | `src/Dialog.tsx:42` | `<button><XIcon /></button>` | Add `aria-label="Close"`; mark the icon `aria-hidden="true"` | The icon-only control has no accessible name |
| HIGH | `src/Nav.tsx:18` | `<a href="/settings"><GearIcon /></a>` | Add `aria-label="Settings"` | The link destination is unavailable to screen readers |

#### Visible focus rings
| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH | `src/button.css:12` | `button:focus { outline: none; }` | `button:focus-visible { outline: 2px solid; outline-offset: 2px; }` | Keyboard users cannot see focus |
| HIGH | `src/Menu.tsx:31` | `focus:outline-none` | `focus-visible:outline-2 focus-visible:outline-offset-2` | Menu navigation has no visible focus indicator |

#### Errors that announce
| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH | `src/EmailField.tsx:27` | Error shown only as `border-red-500` | Add `aria-invalid="true"` + `aria-describedby="email-error"` with inline error text | Color alone neither explains nor announces the error |
| MEDIUM | `src/SignupForm.tsx:64` | Submit disabled until the form is valid | Keep submit enabled; on failure, focus the first invalid field | A disabled action hides what must be fixed |

#### Minimum hit area
| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM | `src/Toolbar.tsx:22` | `size-4` icon-only button | Extend the hit area to 44×44px with `after:absolute after:size-11` | The target is too small for reliable touch input |

### Verification and Verdict

After the findings:

1. **Verification**: list the exact checks run and their observed results, including keyboard traversal, accessible-name inspection, and screen-reader or automated checks when applicable. If a check was not run, state what still needs verification.
2. **Verdict**: `Block` if any `HIGH` finding remains, `Needs changes` if only `MEDIUM` or `LOW` findings remain, and `Approve` only when no actionable findings remain.

When there are no findings, omit the tables, state "No actionable accessibility findings", report verification, and end with `Approve`.
