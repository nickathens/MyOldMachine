# Motion Design

## Should It Animate At All?

The first decision is not which animation, but whether. Gate by how often a user meets the moment:

| Frequency | Decision |
|-----------|----------|
| **100+ times/day** (keyboard shortcuts, command palette, core navigation) | No animation. Ever. |
| **Tens of times/day** (hover states, list navigation, frequent toggles) | Near-imperceptible only: fast and subtle, or nothing |
| **Occasional** (modals, drawers, toasts, settings) | Standard animation |
| **Rare / first-time** (onboarding, empty states, success, celebration) | The delight budget lives here |

**Keyboard-initiated actions are a disqualifier, not a judgment call.** They repeat hundreds of times a day; animation makes them feel slow and disconnected. Raycast has no open/close animation, and that is correct for something opened hundreds of times daily.

Every animation must also name its purpose in one of these words: **feedback** (the interface heard you), **spatial consistency** (where something came from or went), **state indication** (a change made legible), **preventing a jarring change** (bridging content that would otherwise teleport), **explanation** (marketing and onboarding only), or **delight** (allowed only at the rare/first-time tier). "It looks cool" on a frequently seen element is a reason to stop. Data the user is reading or acting on should not move for style: a decorative effect belongs on a marketing page, not on a chart in a dashboard.

## Duration: The 100/300/500 Rule

Timing matters more than easing. These durations feel right for most UI:

| Duration | Use Case | Examples |
|----------|----------|----------|
| **100-150ms** | Instant feedback | Button press, toggle, color change |
| **200-300ms** | State changes | Menu open, tooltip, hover states |
| **300-500ms** | Layout changes | Accordion, modal, drawer |
| **500-800ms** | Entrance animations | Page load, hero reveals |

**Exit animations are faster than entrances**—use ~75% of enter duration.

## Easing: Pick the Right Curve

**Don't use `ease`.** It's a compromise that's rarely optimal. Instead:

| Curve | Use For | CSS |
|-------|---------|-----|
| **ease-out** | Elements entering | `cubic-bezier(0.16, 1, 0.3, 1)` |
| **ease-in** | Elements leaving | `cubic-bezier(0.7, 0, 0.84, 0)` |
| **ease-in-out** | State toggles (there → back) | `cubic-bezier(0.65, 0, 0.35, 1)` |

**For micro-interactions, use exponential curves**—they feel natural because they mimic real physics (friction, deceleration):

```css
/* Quart out - smooth, refined (recommended default) */
--ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);

/* Quint out - slightly more dramatic */
--ease-out-quint: cubic-bezier(0.22, 1, 0.36, 1);

/* Expo out - snappy, confident */
--ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
```

**Avoid bounce and elastic curves.** They were trendy in 2015 but now feel tacky and amateurish. Real objects don't bounce when they stop—they decelerate smoothly. Overshoot effects draw attention to the animation itself rather than the content.

## Origin: Where Motion Starts From

**Never `scale(0)`.** Nothing in the real world appears from nothing. Enter from `scale(0.9)` to `scale(0.97)` plus `opacity: 0`.

**Popovers, dropdowns, menus, and tooltips scale from their trigger, not from their own center.** Set `transform-origin` to the trigger side (Base UI exposes `var(--transform-origin)` for exactly this). **Modals are exempt**: they are not anchored to a trigger, so they keep `transform-origin: center`.

**Exit the way it entered.** A toast that slides in from the bottom leaves through the bottom. Symmetric paths keep the user's spatial map of the interface intact, and they are what make swipe-to-dismiss feel obvious.

## Interruptibility

Anything a user can trigger twice in a second (toasts stacking, toggles, rapid open/close) must retarget smoothly from its current state instead of restarting.

- **CSS transitions retarget; `@keyframes` restart from zero.** Use transitions for rapidly triggered UI; reserve keyframes for predetermined motion that runs once (entrances, reveals).
- **`@starting-style` gives an entry animation without JS** while staying a transition, so it remains interruptible:

```css
.toast {
  opacity: 1; transform: translateY(0);
  transition: opacity 400ms ease, transform 400ms ease;
  @starting-style { opacity: 0; transform: translateY(100%); }
}
```

- **Springs carry velocity through an interruption**, which fixed-duration curves cannot: a flicked element keeps its speed when redirected instead of stopping and restarting. Use them for gesture-driven motion the user may reverse mid-flight (drag-to-dismiss, sheets). Keep any bounce subtle and out of everyday UI; the no-bounce rule above still holds for non-gesture motion.

## The Only Two Properties You Should Animate

**transform** and **opacity** only—everything else causes layout recalculation. For height animations (accordions), use `grid-template-rows: 0fr → 1fr` instead of animating `height` directly.

## Staggered Animations

Use CSS custom properties for cleaner stagger: `animation-delay: calc(var(--i, 0) * 50ms)` with `style="--i: 0"` on each item. **Cap total stagger time**—10 items at 50ms = 500ms total. For many items, reduce per-item delay or cap staggered count.

## Reduced Motion

This is not optional. Vestibular disorders affect ~35% of adults over 40.

```css
/* Define animations normally */
.card {
  animation: slide-up 500ms ease-out;
}

/* Provide alternative for reduced motion */
@media (prefers-reduced-motion: reduce) {
  .card {
    animation: fade-in 200ms ease-out;  /* Crossfade instead of motion */
  }
}

/* Or disable entirely */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

**What to preserve**: Functional animations like progress bars, loading spinners (slowed down), and focus indicators should still work—just without spatial movement.

## Perceived Performance

**Nobody cares how fast your site is—just how fast it feels.** Perception can be as effective as actual performance.

**The 80ms threshold**: Our brains buffer sensory input for ~80ms to synchronize perception. Anything under 80ms feels instant and simultaneous. This is your target for micro-interactions.

**Active vs passive time**: Passive waiting (staring at a spinner) feels longer than active engagement. Strategies to shift the balance:

- **Preemptive start**: Begin transitions immediately while loading (iOS app zoom, skeleton UI). Users perceive work happening.
- **Early completion**: Show content progressively—don't wait for everything. Video buffering, progressive images, streaming HTML.
- **Optimistic UI**: Update the interface immediately, handle failures gracefully. Instagram likes work offline—the UI updates instantly, syncs later. Use for low-stakes actions; avoid for payments or destructive operations.

**Easing affects perceived duration**: Ease-in (accelerating toward completion) makes tasks feel shorter because the peak-end effect weights final moments heavily. Ease-out feels satisfying for entrances, but ease-in toward a task's end compresses perceived time.

**Caution**: Too-fast responses can decrease perceived value. Users may distrust instant results for complex operations (search, analysis). Sometimes a brief delay signals "real work" is happening.

## Performance

Don't use `will-change` preemptively—only when animation is imminent (`:hover`, `.animating`). For scroll-triggered animations, use Intersection Observer instead of scroll events; unobserve after animating once. Create motion tokens for consistency (durations, easings, common transitions).

---

**Avoid**: Animating everything (animation fatigue is real). Animating keyboard-initiated actions or anything used 100+ times a day. Entrances from `scale(0)`. Keyframes on rapidly triggered elements. Using >500ms for UI feedback. Ignoring `prefers-reduced-motion`. Using animation to hide slow loading.

---

The frequency gate, purpose vocabulary, origin rules, and interruptibility rules are adapted from [emilkowalski/skills](https://github.com/emilkowalski/skills) (MIT, commit `78761e1`). Licence text and fold notes: `animation-vocabulary.md`, Source and Licence section. For the exact name of a motion effect, see `animation-vocabulary.md`.
