#!/usr/bin/env python3
"""
Watch a page run and record what changes, when, and in answer to what.

Usage:
  python watch_page.py --url https://site.com --seconds 30 --out run.json
  python watch_page.py --url https://site.com --seconds 25 --control \\
      --action "3:wheel:600" --action "8:click:button[aria-label='Next slide']"
  python watch_page.py --url https://site.com --reduced-motion --shots /tmp/shots

What it answers: is a moving part driven by time, by scroll, by the wheel, by a
click, or by a key? A static read of the code suggests an answer; this watches
the page and records the evidence, so a behavioural claim can be confirmed.

A recorder is injected before any page script runs. In the page's own clock
(milliseconds since navigation start) it logs:

  class / style / attr / child   DOM mutations, each tied to a stable node id
  media                          play, pause, ended, seeked, volumechange on
                                 every <video>/<audio>, with currentTime
  transition / animation         CSS transitions and keyframe animations as
                                 they start and end, with the property and the
                                 elapsed time the browser reports
  timer                          every setTimeout/setInterval scheduled with a
                                 delay of at least --min-delay ms (the delay
                                 is the page's own number), logged when set,
                                 fired and cleared; string callbacks are not
                                 logged
  input / scroll                 wheel, click, key and touch events the page
                                 received (with the element that got them),
                                 and every scroll position change, down and
                                 sideways
  raf                            requestAnimationFrame calls per second, which
                                 separates a JavaScript animation loop from a
                                 CSS transition

Nodes are labelled by tag, id, first three classes and aria-label as they
looked when first seen, so a slide that later changes class keeps its first
label. It also snapshots the scroll layout once the run ends: whether the document
can scroll at all, inner scroll containers (down, sideways or both), sticky and
fixed elements, scroll snapping, and CSS scroll-driven animations
(animation-timeline).

--action T:KIND[:ARG] performs an input T seconds after the page settles:
  wheel:DY  scroll:Y  click:SELECTOR  key:KEY  hover:SELECTOR
The pointer rests at the centre of the viewport from the moment the page loads,
in every run, the idle control included, so a wheel lands on whatever is in the
middle of the screen; a hover or a click moves it. (Left at the page's top-left
corner, a wheel on example.com landed on the site header, not the reel.)
--control first runs the same page with NO actions for the same time, then
compares each action's window (T to T + --window ms) across the two runs. A
change that happens in both runs is the page's own clock; a change only in the
action run answered the input. Each comparison names the elements that received
the input, and an action that failed (a selector never found) gets no verdict.

Trust boundary: this EXECUTES the page's JavaScript in a fresh, throwaway
browser context, in headless Chromium on this machine, inside Chromium's own
sandbox (own user and network namespace, seccomp filter; on this Ubuntu that
needs /etc/apparmor.d/playwright-chromium, and without it the launch fails
rather than run unsandboxed). That is one layer, not a container. Point it only
at a page you are allowed to analyse (a public page, your own work, a
client's). Code you have reason to think is hostile is never run here.

Every logged record is ground truth from a runtime observation, so a
behavioural claim built on it may be marked confirmed with method "runtime".
What a change means (which component, which library) stays a hypothesis.
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict

MAX_SECONDS = 600

RECORDER_JS = r"""
(() => {
  if (window.__reWatch) return;
  const MAX = __MAX_EVENTS__, MIN_DELAY = __MIN_DELAY__;
  const W = window.__reWatch = {log: [], dropped: {}, nodes: {}, raf: {}};
  const now = () => Math.round(performance.now());
  const cut = (s, n) => (s == null ? null : String(s).slice(0, n || 160));
  const ids = new WeakMap();
  let next = 1;
  const label = (el) => {
    if (!el || el.nodeType !== 1) return String(el && el.nodeName);
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).filter(Boolean);
    if (cls.length) s += '.' + cls.slice(0, 3).join('.');
    const aria = el.getAttribute && el.getAttribute('aria-label');
    if (aria) s += '[aria-label="' + cut(aria, 40) + '"]';
    return s;
  };
  const nid = (el) => {
    if (!el || el.nodeType !== 1) return el === document ? 'document' : null;
    let id = ids.get(el);
    if (!id) {
      id = next++;
      ids.set(el, id);
      const chain = [];
      let p = el.parentElement, d = 0;
      while (p && d < 3) { chain.unshift(label(p)); p = p.parentElement; d++; }
      W.nodes[id] = {label: label(el), parents: chain};
    }
    return id;
  };
  const rec = (r) => {
    if (W.log.length >= MAX) { W.dropped[r.k] = (W.dropped[r.k] || 0) + 1; return; }
    r.ms = now();
    W.log.push(r);
  };
  W.nid = nid;
  W.rec = rec;

  // Timers: the page's own delays, logged when set, when they fire and when
  // they are cleared, so a timer that is set and then cancelled is never
  // mistaken for the one that drives the page. The real functions still run.
  const st = window.setTimeout, si = window.setInterval;
  const ct = window.clearTimeout, ci = window.clearInterval;
  const live = new Map();
  const wrap = (kind, orig) => function (fn, d, ...a) {
    const ms = Number(d) || 0;
    if (ms < MIN_DELAY || typeof fn !== 'function') return orig.call(this, fn, d, ...a);
    let id;
    const cb = function (...args) {
      rec({k: 'timer', ev: 'fire', kind, delay: ms, id});
      if (kind === 'timeout') live.delete(id);
      return fn.apply(this, args);
    };
    id = orig.call(this, cb, d, ...a);
    live.set(id, {kind, delay: ms});
    rec({k: 'timer', ev: 'set', kind, delay: ms, id});
    return id;
  };
  window.setTimeout = wrap('timeout', st);
  window.setInterval = wrap('interval', si);
  const unwrap = (orig) => function (id) {
    const t = live.get(id);
    if (t) {
      rec({k: 'timer', ev: 'clear', kind: t.kind, delay: t.delay, id});
      live.delete(id);
    }
    return orig.call(this, id);
  };
  window.clearTimeout = unwrap(ct);
  window.clearInterval = unwrap(ci);

  // requestAnimationFrame calls per whole second of page time.
  const raf = window.requestAnimationFrame;
  if (raf) {
    window.requestAnimationFrame = function (cb) {
      const s = Math.floor(performance.now() / 1000);
      W.raf[s] = (W.raf[s] || 0) + 1;
      return raf.call(this, cb);
    };
  }

  const ATTRS = ['class', 'style', 'src', 'hidden', 'aria-current', 'aria-hidden', 'aria-selected',
                 'aria-pressed', 'data-state', 'open', 'muted'];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'NOSCRIPT']);
  const mo = new MutationObserver((list) => {
    // Within one batch the same attribute can change several times. Each
    // record's new value is the next record's old value; only the last one
    // compares against the live attribute.
    const newValues = new Array(list.length);
    const nextOld = new Map();
    for (let i = list.length - 1; i >= 0; i--) {
      const m = list[i];
      if (m.type !== 'attributes') continue;
      let perTarget = nextOld.get(m.target);
      if (!perTarget) { perTarget = new Map(); nextOld.set(m.target, perTarget); }
      newValues[i] = perTarget.has(m.attributeName) ? perTarget.get(m.attributeName)
                                                   : m.target.getAttribute(m.attributeName);
      perTarget.set(m.attributeName, m.oldValue);
    }
    for (let i = 0; i < list.length; i++) {
      const m = list[i];
      const t = m.target;
      if (m.type === 'attributes') {
        const nv = newValues[i];
        if (m.attributeName === 'class') {
          const a = new Set((m.oldValue || '').split(/\s+/).filter(Boolean));
          const b = new Set((nv || '').split(/\s+/).filter(Boolean));
          const added = [...b].filter(x => !a.has(x)), removed = [...a].filter(x => !b.has(x));
          if (added.length || removed.length) rec({k: 'class', node: nid(t), added, removed});
        } else if (m.attributeName === 'style') {
          if (nv !== m.oldValue) rec({k: 'style', node: nid(t), value: cut(nv)});
        } else if (nv !== m.oldValue) {
          rec({k: 'attr', node: nid(t), name: m.attributeName, value: cut(nv)});
        }
      } else if (m.type === 'childList') {
        const add = [...m.addedNodes].filter(n => n.nodeType === 1 && !SKIP.has(n.nodeName));
        const rem = [...m.removedNodes].filter(n => n.nodeType === 1 && !SKIP.has(n.nodeName));
        if (add.length || rem.length) {
          rec({k: 'child', node: nid(t), added: add.slice(0, 5).map(nid), removed: rem.length});
        }
      }
    }
  });
  mo.observe(document, {subtree: true, childList: true, attributes: true,
                        attributeOldValue: true, attributeFilter: ATTRS});

  // Media events do not bubble, but a capturing listener on the document sees them.
  const base = (s) => (s || '').split('?')[0].split('/').pop();
  for (const ev of ['play', 'playing', 'pause', 'ended', 'seeked', 'volumechange', 'loadeddata']) {
    document.addEventListener(ev, (e) => {
      const v = e.target;
      if (!(v instanceof HTMLMediaElement)) return;
      rec({k: 'media', ev, node: nid(v), src: base(v.currentSrc || v.src),
           t: Math.round(v.currentTime * 1000) / 1000, muted: v.muted});
    }, true);
  }
  for (const ev of ['transitionstart', 'transitionend', 'transitioncancel']) {
    document.addEventListener(ev, (e) => rec({k: 'transition', ev, node: nid(e.target),
      prop: e.propertyName, elapsed: Math.round(e.elapsedTime * 1000) / 1000}), true);
  }
  for (const ev of ['animationstart', 'animationend']) {
    document.addEventListener(ev, (e) => rec({k: 'animation', ev, node: nid(e.target),
      name: e.animationName, elapsed: Math.round(e.elapsedTime * 1000) / 1000}), true);
  }
  for (const ev of ['wheel', 'click', 'keydown', 'touchstart']) {
    window.addEventListener(ev, (e) => rec({k: 'input', ev, node: nid(e.target),
      dy: ev === 'wheel' ? e.deltaY : undefined, key: ev === 'keydown' ? e.key : undefined}),
      {capture: true, passive: true});
  }
  const lastScroll = new Map();
  document.addEventListener('scroll', (e) => {
    const tgt = e.target === document ? document.scrollingElement : e.target;
    const key = e.target === document ? 'document' : nid(e.target);
    const t = performance.now();
    if (t - (lastScroll.get(key) || -1e9) < 100) return;
    lastScroll.set(key, t);
    rec({k: 'scroll', node: key, top: Math.round(tgt ? tgt.scrollTop : 0), left: Math.round(tgt ? tgt.scrollLeft : 0)});
  }, true);
})();
"""

LAYOUT_JS = r"""
() => {
  const de = document.documentElement;
  const W = window.__reWatch;
  // The viewport takes html's overflow, or body's when html's is visible (CSS overflow propagation).
  const hov = getComputedStyle(de).overflowY;
  const bov = document.body ? getComputedStyle(document.body).overflowY : 'visible';
  const vov = hov !== 'visible' ? hov : bov;
  const out = {doc_scroll_height: de.scrollHeight, viewport_height: innerHeight, viewport_overflow_y: vov,
               doc_scrollable: de.scrollHeight > innerHeight + 1 && vov !== 'hidden' && vov !== 'clip',
               scroll_containers: [], sticky: [], fixed: 0, scroll_snap: [], scroll_timelines: [],
               elements_checked: 0};
  let n = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (++n > 20000) break;
    const cs = getComputedStyle(el);
    const id = W ? W.nid(el) : null;
    if (cs.position === 'sticky' && out.sticky.length < 20) out.sticky.push(id);
    if (cs.position === 'fixed') out.fixed++;
    if (cs.scrollSnapType && cs.scrollSnapType !== 'none' && out.scroll_snap.length < 20)
      out.scroll_snap.push({node: id, type: cs.scrollSnapType});
    const tl = cs.animationTimeline;
    if (tl && tl !== 'auto' && out.scroll_timelines.length < 20)
      out.scroll_timelines.push({node: id, timeline: tl});
    const sy = (cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1;
    const sx = (cs.overflowX === 'auto' || cs.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;
    if ((sx || sy) && out.scroll_containers.length < 20)
      out.scroll_containers.push({node: id, axis: sx && sy ? 'xy' : (sx ? 'x' : 'y'),
                                  scroll_height: el.scrollHeight, client_height: el.clientHeight,
                                  scroll_width: el.scrollWidth, client_width: el.clientWidth});
  }
  out.elements_checked = n;
  return out;
}
"""


def parse_action(spec):
    """'3.5:click:button.next' -> (3.5, 'click', 'button.next'). Raises ValueError."""
    parts = spec.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"action {spec!r}: expected T:KIND[:ARG]")
    t = float(parts[0])
    kind = parts[1].strip().lower()
    arg = parts[2] if len(parts) > 2 else ""
    if t < 0:
        raise ValueError(f"action {spec!r}: time must be >= 0")
    if kind not in ("wheel", "scroll", "click", "key", "hover"):
        raise ValueError(f"action {spec!r}: kind must be wheel, scroll, click, key or hover")
    if kind in ("wheel", "scroll"):
        float(arg or "0")
    elif not arg:
        raise ValueError(f"action {spec!r}: {kind} needs an argument")
    return t, kind, arg


def perform(page, kind, arg):
    if kind == "wheel":
        page.mouse.wheel(0, float(arg or 120))
    elif kind == "scroll":
        page.evaluate("y => window.scrollTo(0, y)", float(arg or 0))
    elif kind == "click":
        page.click(arg, timeout=5000)
    elif kind == "key":
        page.keyboard.press(arg)
    elif kind == "hover":
        page.hover(arg, timeout=5000)


def run_once(browser, url, seconds, viewport, reduced, actions, settle_ms, max_events, min_delay,
             shots=None, shot_every=0.0):
    ctx = browser.new_context(viewport=viewport, reduced_motion="reduce" if reduced else "no-preference",
                              accept_downloads=False)
    page = ctx.new_page()
    script = RECORDER_JS.replace("__MAX_EVENTS__", str(int(max_events))).replace(
        "__MIN_DELAY__", str(int(min_delay)))
    page.add_init_script(script)
    errors = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:  # noqa: BLE001 - keep whatever loaded; the log says what happened
        errors.append(f"navigation: {str(e)[:200]}")
    # Before the settle, in every run alike, so any hover effect it causes is over before the clock starts.
    page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)
    page.wait_for_timeout(settle_ms)
    start_wall = time.monotonic()
    start_ms = page.evaluate("Math.round(performance.now())")
    done = []
    pending = sorted(actions, key=lambda a: a[0])
    next_shot = 0.0
    while True:
        elapsed = time.monotonic() - start_wall
        if elapsed >= seconds:
            break
        if pending and elapsed >= pending[0][0]:
            t, kind, arg = pending.pop(0)
            at_ms = page.evaluate("Math.round(performance.now())")
            ok, err = True, None
            try:
                perform(page, kind, arg)
            except Exception as e:  # noqa: BLE001 - a missing selector is a result, not a crash
                ok, err = False, str(e).splitlines()[0][:200]
            done.append({"t": t, "kind": kind, "arg": arg, "page_ms": at_ms, "ok": ok, "error": err})
            continue
        if shots and shot_every > 0 and elapsed >= next_shot:
            page.screenshot(path=f"{shots}/t{int(elapsed * 1000):06d}.png")
            next_shot += shot_every
            continue
        waits = [seconds - elapsed]
        if pending:
            waits.append(pending[0][0] - elapsed)
        if shots and shot_every > 0:
            waits.append(next_shot - elapsed)
        page.wait_for_timeout(max(10, min(waits) * 1000))
    data = page.evaluate("JSON.stringify(window.__reWatch || null)")
    layout = page.evaluate(LAYOUT_JS)
    nodes_after = page.evaluate("JSON.stringify(window.__reWatch ? window.__reWatch.nodes : {})")
    ctx.close()
    rec = json.loads(data) if data else {"log": [], "dropped": {}, "nodes": {}, "raf": {}}
    rec["nodes"] = json.loads(nodes_after) if nodes_after else rec.get("nodes", {})
    return {"start_ms": start_ms, "end_ms": start_ms + int(seconds * 1000), "actions": done,
            "layout": layout, "errors": errors, **rec}


def node_label(run, nid):
    if nid in (None, "document"):
        return str(nid)
    n = run["nodes"].get(str(nid))
    return n["label"] if n else f"node{nid}"


def summarize(run):
    """Describe the whole recording, page load included: a timer or a first play
    scheduled while the page loads is part of the mechanism."""
    log = [r for r in run["log"] if r["ms"] <= run["end_ms"]]
    s = {"events": dict(Counter(r["k"] for r in log)), "dropped": run.get("dropped", {}),
         "watch_started_ms": run["start_ms"]}
    plays = [r for r in log if r["k"] == "media" and r["ev"] == "play"]
    s["media_plays"] = [{"ms": r["ms"], "src": r["src"], "t": r["t"]} for r in plays]
    starts = [r["ms"] for r in plays]
    s["media_play_intervals_ms"] = [b - a for a, b in zip(starts, starts[1:])]
    by_node = defaultdict(list)
    for r in log:
        if r["k"] == "class":
            by_node[r["node"]].append(r["ms"])
    toggles = []
    for nid, ms in sorted(by_node.items(), key=lambda kv: -len(kv[1])):
        gaps = [b - a for a, b in zip(ms, ms[1:])]
        toggles.append({"node": nid, "label": node_label(run, nid), "changes": len(ms),
                        "median_gap_ms": statistics.median(gaps) if gaps else None})
    s["class_toggles"] = toggles[:12]
    timers, unlabelled = defaultdict(Counter), 0
    for r in log:
        if r["k"] == "timer":
            if "ev" in r:
                timers[(r["kind"], r["delay"])][r["ev"]] += 1
            else:  # an earlier recorder logged timers without set, fire or clear
                unlabelled += 1
    s["timers"] = [{"kind": k, "delay_ms": d, "set": c["set"], "fired": c["fire"], "cleared": c["clear"]}
                   for (k, d), c in sorted(timers.items(), key=lambda kv: -sum(kv[1].values()))[:15]]
    if unlabelled:
        s["timers_unlabelled"] = unlabelled
    tr = Counter((node_label(run, r["node"]), r["prop"], r["elapsed"]) for r in log
                 if r["k"] == "transition" and r["ev"] == "transitionend")
    s["transitions_ended"] = [{"node": n, "property": p, "elapsed_s": e, "count": c}
                              for (n, p, e), c in tr.most_common(12)]
    secs = range(run["start_ms"] // 1000, run["end_ms"] // 1000)  # steady state only, after settling
    raf = [run.get("raf", {}).get(str(x), 0) for x in secs]
    s["raf_per_second"] = round(sum(raf) / len(raf), 1) if raf else 0
    s["scroll_events"] = sum(1 for r in log if r["k"] == "scroll")
    s["layout"] = run["layout"]
    return s


def window_counts(run, start, width):
    c = Counter()
    for r in run["log"]:
        if start <= r["ms"] < start + width and r["k"] not in ("input", "timer"):
            c[(r["k"], node_label(run, r.get("node")))] += 1
    return c


def compare(action_run, control_run, width, margin=250):
    """For each action, what changed in its window that did not change in the
    same window of the idle control run (aligned on the settle point).

    The control window is widened by `margin` ms on both sides, so a clock tick
    that lands a few ms later in one run than the other cannot pass for an
    answer to the input. A (kind, node) pair counts as an answer when it is
    absent from the widened control window, or when it fires well beyond
    jitter (at least twice the control count plus 3): a per-frame animation
    that runs in both windows differs by a few frames from run to run, and that
    difference is not an answer (found on example.com, 2026-10-12). This
    leans toward "no change": a change that both the clock and the input make
    to the same node can hide. ponytail: counts per (kind, node); a
    per-property diff would separate a shared node."""
    out = []
    shift = control_run["start_ms"] - action_run["start_ms"]
    for a in action_run["actions"]:
        w0 = a["page_ms"]
        got = window_counts(action_run, w0, width)
        base = window_counts(control_run, w0 + shift - margin, width + 2 * margin)
        new_pairs = {f"{k}:{n}": c for (k, n), c in got.items() if (k, n) not in base}
        surplus = {f"{k}:{n}": c - base[(k, n)] for (k, n), c in got.items()
                   if (k, n) in base and c >= 2 * base[(k, n)] + 3}
        answered = bool(new_pairs or surplus)
        # Which elements the input reached: a wheel over the header says nothing about the reel.
        received = sorted({node_label(action_run, r.get("node")) for r in action_run["log"]
                           if r["k"] == "input" and w0 <= r["ms"] < w0 + width})
        if not a["ok"]:
            verdict = "action failed: no verdict"
        else:
            verdict = "answered the input" if answered else "no change beyond the page's own clock"
        out.append({"action": f'{a["t"]}:{a["kind"]}:{a["arg"]}', "ok": a["ok"],
                    "changes_in_window": sum(got.values()), "control_changes_in_window": sum(base.values()),
                    "only_after_action": dict(sorted(new_pairs.items(), key=lambda kv: -kv[1])[:10]),
                    "far_more_after_action": dict(sorted(surplus.items(), key=lambda kv: -kv[1])[:10]),
                    "received_by": received, "verdict": verdict})
    return out


def print_summary(name, s):
    print(f"== {name}")
    lay = s["layout"]
    print(f"  document scrolls: {lay['doc_scrollable']} (height {lay['doc_scroll_height']} vs viewport "
          f"{lay['viewport_height']}); inner scroll containers {len(lay['scroll_containers'])}; "
          f"sticky {len(lay['sticky'])}; scroll snap {len(lay['scroll_snap'])}; "
          f"scroll-driven CSS animations {len(lay['scroll_timelines'])}")
    print(f"  events: {s['events']}  dropped: {s['dropped'] or 'none'}")
    print(f"  requestAnimationFrame per second: {s['raf_per_second']}")
    for p in s["media_plays"][:12]:
        print(f"  play  {p['ms']:>7} ms  {p['src']}  at t={p['t']}")
    if s["media_play_intervals_ms"]:
        print(f"  gaps between plays (ms): {s['media_play_intervals_ms'][:12]}")
    for t in s["timers"][:8]:
        print(f"  timer {t['kind']:8} {t['delay_ms']:>7} ms  set {t['set']}, fired {t['fired']}, "
              f"cleared {t['cleared']}")
    if s.get("timers_unlabelled"):
        print(f"  timer records from an older recorder, with no set/fire/clear: {s['timers_unlabelled']}")
    for t in s["transitions_ended"][:6]:
        print(f"  transition {t['property']} {t['elapsed_s']}s on {t['node']} x{t['count']}")
    for t in s["class_toggles"][:6]:
        print(f"  class changes x{t['changes']:<3} median gap {t['median_gap_ms']} ms  {t['label'][:90]}")


def main():
    ap = argparse.ArgumentParser(description="Watch a page run and record what changes and why.")
    ap.add_argument("--url", required=True, help="Page to watch (its JavaScript WILL run).")
    ap.add_argument("--seconds", type=float, default=20.0, help="How long to watch after settling.")
    ap.add_argument("--settle-ms", type=int, default=2000, help="Wait after load before the clock starts.")
    ap.add_argument("--viewport", default="1440x900", help="WIDTHxHEIGHT (default 1440x900).")
    ap.add_argument("--reduced-motion", action="store_true", help="Emulate prefers-reduced-motion: reduce.")
    ap.add_argument("--action", action="append", default=[],
                    help="T:KIND[:ARG], repeatable. A wheel fires where the pointer is: the centre of the "
                         "viewport, or the element the last hover or click went to.")
    ap.add_argument("--control", action="store_true",
                    help="Also run an idle control and compare each action window against it.")
    ap.add_argument("--window", type=int, default=1500, help="Action window in ms (default 1500).")
    ap.add_argument("--margin", type=int, default=250,
                    help="Widen the control window by this many ms on each side (default 250).")
    ap.add_argument("--min-delay", type=int, default=250, help="Smallest timer delay to log (ms).")
    ap.add_argument("--max-events", type=int, default=20000, help="Cap on logged records per run.")
    ap.add_argument("--shots", help="Directory for screenshots of the action run.")
    ap.add_argument("--shot-every", type=float, default=0.0, help="Seconds between screenshots.")
    ap.add_argument("--out", help="Write the full JSON here.")
    args = ap.parse_args()

    if not 0 < args.seconds <= MAX_SECONDS:
        ap.error(f"--seconds must be between 0 and {MAX_SECONDS}")
    try:
        w, h = (int(x) for x in args.viewport.lower().split("x"))
        actions = [parse_action(a) for a in args.action]
    except ValueError as e:
        ap.error(str(e))
    if any(t >= args.seconds for t, _, _ in actions):
        ap.error("every action must fall inside --seconds")
    if args.control and not actions:
        ap.error("--control compares action windows; give at least one --action")
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)

    from playwright.sync_api import sync_playwright

    result = {"url": args.url, "viewport": [w, h], "reduced_motion": args.reduced_motion,
              "seconds": args.seconds, "settle_ms": args.settle_ms}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True,
                                        args=["--autoplay-policy=no-user-gesture-required"])
            common = dict(url=args.url, seconds=args.seconds, viewport={"width": w, "height": h},
                          reduced=args.reduced_motion, settle_ms=args.settle_ms,
                          max_events=args.max_events, min_delay=args.min_delay)
            if args.control:
                result["control"] = run_once(browser, actions=[], **common)
            result["run"] = run_once(browser, actions=actions, shots=args.shots,
                                     shot_every=args.shot_every, **common)
            browser.close()
    except Exception as e:  # noqa: BLE001 - launch/runtime failures get a hint, not a traceback
        print(f"Watch failed: {e}", file=sys.stderr)
        print("If Chromium is missing: python skills/browser/scripts/browser.py status",
              file=sys.stderr)
        print("If Chromium died at launch (SIGTRAP): it could not start its own sandbox. On Ubuntu 23.10+ that "
              "needs the AppArmor profile /etc/apparmor.d/playwright-chromium (reverse-engineering SKILL.md). "
              "A memory ulimit (ulimit -v) kills it the same way.", file=sys.stderr)
        sys.exit(1)

    result["summary"] = summarize(result["run"])
    if args.control:
        result["control_summary"] = summarize(result["control"])
        result["comparison"] = compare(result["run"], result["control"], args.window, args.margin)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        print(f"Wrote {args.out}")
    if args.control:
        print_summary("control (no input)", result["control_summary"])
    print_summary("run" + (" (with actions)" if actions else ""), result["summary"])
    for a in result["run"]["actions"]:
        if not a["ok"]:
            print(f"  action {a['t']}:{a['kind']}:{a['arg']} FAILED: {a['error']}")
    for c in result.get("comparison", []):
        print(f"  {c['action']}: {c['verdict']} ({c['changes_in_window']} changes vs "
              f"{c['control_changes_in_window']} in control; received by {', '.join(c['received_by']) or 'nothing'})"
              f" {c['only_after_action'] or ''}"
              f"{' far more: ' + str(c['far_more_after_action']) if c['far_more_after_action'] else ''}")
    for e in result["run"]["errors"]:
        print(f"  warning: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
