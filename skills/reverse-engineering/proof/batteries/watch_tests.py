# Semicolon-chained steps and grouped imports are this skill's house style (the
# donor repo exempts E401/E701/E702/E741 under skills/; MOM lints skills/
# globally, so the exemption is pinned per file here).
# ruff: noqa: E401, E702
"""Planted-fault battery for tests/test_reverse_engineering_watch.py.

Each arm mutates a COPY of watch_page.py in a temp tree; the real file is never
touched. Verdicts: CAUGHT (the named test failed), SURVIVED (all passed),
WRONG (failed, but not the named test), CRASHED (no pytest summary).
The NULL arm changes a comment only and must survive.

  systemd-run --user --scope -p MemoryMax=4G python watch_tests.py [ARM NAME ...]

Last run 2026-10-12, after the audit fixes: 14/14 fault arms CAUGHT by their named test, NULL SURVIVED.
"""
import os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = str(Path(__file__).resolve().parents[4])
SRC = f'{REPO}/skills/reverse-engineering/scripts/watch_page.py'
TEST = f'{REPO}/tests/test_reverse_engineering_watch.py'
ARMS = [
    ('compare always answers', '        answered = bool(new_pairs or surplus)', '        answered = True',
     'test_wheel_on_a_page_that_ignores_it_changes_nothing'),
    ('clear not logged', "      rec({k: 'timer', ev: 'clear', kind: t.kind, delay: t.delay, id});", '',
     'test_cancelled_timer_is_not_counted_as_fired'),
    ('media listener not capturing', "           t: Math.round(v.currentTime * 1000) / 1000, muted: v.muted});\n    }, true);",
     "           t: Math.round(v.currentTime * 1000) / 1000, muted: v.muted});\n    }, false);",
     'test_video_play_is_logged_with_its_source'),
    ('sticky never detected', "if (cs.position === 'sticky' &&", "if (cs.position === 'stickyx' &&",
     'test_scroll_layout_flags'),
    ('old batch code', '        const nv = newValues[i];', '        const nv = t.getAttribute(m.attributeName);',
     'test_two_writes_in_one_batch_are_told_apart'),
    ('jitter rule reverted', 'if (k, n) in base and c >= 2 * base[(k, n)] + 3}', 'if (k, n) in base and c > base[(k, n)]}',
     'test_a_few_more_animation_frames_is_not_an_answer'),
    ('transitionstart counted', '''if r["k"] == "transition" and r["ev"] == "transitionend")''',
     '''if r["k"] == "transition" and r["ev"] == "transitionstart")''', 'test_transition_reports_property_and_duration'),
    ('overflow rule removed', "doc_scrollable: de.scrollHeight > innerHeight + 1 && vov !== 'hidden' && vov !== 'clip',",
     "doc_scrollable: de.scrollHeight > innerHeight + 1,", 'test_two_writes_in_one_batch_are_told_apart'),
    ('pointer left in the corner', '    page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)\n', '',
     'test_wheel_reaches_the_middle_of_the_screen'),
    ('received_by dropped', '"received_by": received, ', '', 'test_wheel_reaches_the_middle_of_the_screen'),
    ('failed action judged', '        if not a["ok"]:\n            verdict = "action failed: no verdict"',
     '        if False:\n            verdict = "action failed: no verdict"', 'test_a_failed_action_gets_no_verdict'),
    ('sideways scroll ignored',
     "    const sx = (cs.overflowX === 'auto' || cs.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;",
     '    const sx = false;', 'test_a_sideways_scroller_is_listed_with_its_axis'),
    ('scroll left not logged', ', left: Math.round(tgt ? tgt.scrollLeft : 0)});', '});',
     'test_a_sideways_scroller_is_listed_with_its_axis'),
    ('old timer records crash',
     '            if "ev" in r:\n                timers[(r["kind"], r["delay"])][r["ev"]] += 1\n'
     '            else:  # an earlier recorder logged timers without set, fire or clear\n                unlabelled += 1',
     '            timers[(r["kind"], r["delay"])][r["ev"]] += 1',
     'test_timer_records_without_an_event_are_counted_apart'),
    ('NULL control (comment only)', '// Media events do not bubble', '// Media events (reworded) do not bubble', None),
]
only = sys.argv[1:]
src = open(SRC).read()
results = []
for name, old, new, expect in ARMS:
    if only and name not in only:
        continue
    n = src.count(old)
    if n != 1:
        results.append((name, f'VOID (anchor found {n} times)', ''))
        continue
    with tempfile.TemporaryDirectory(prefix='re-battery-') as tmp:
        os.makedirs(f'{tmp}/tests'); os.makedirs(f'{tmp}/skills/reverse-engineering/scripts')
        shutil.copy(TEST, f'{tmp}/tests/')
        mutated = src.replace(old, new)
        assert mutated != src
        open(f'{tmp}/skills/reverse-engineering/scripts/watch_page.py', 'w').write(mutated)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        try:
            p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--tb=no', '-rf',
                                f'{tmp}/tests/test_reverse_engineering_watch.py'], capture_output=True, text=True,
                               timeout=300, env=env, cwd=tmp)
            out = p.stdout
        except subprocess.TimeoutExpired:
            results.append((name, 'TIMEOUT', '')); continue
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    summary = next((ln for ln in reversed(lines) if re.search(r'\d+ (passed|failed)', ln)), None)
    failed = [ln.split('::')[-1].split(' ')[0] for ln in lines if ln.startswith('FAILED')]
    if summary is None:
        verdict = 'CRASHED'
    elif not failed:
        verdict = 'SURVIVED'
    elif expect and expect in failed:
        verdict = 'CAUGHT'
    else:
        verdict = 'WRONG'
    results.append((name, verdict, f'{summary} | failed: {", ".join(failed)}'))
    print(f'{name:32} {verdict:9} {summary} | failed: {", ".join(failed)}', flush=True)
caught = sum(1 for _, v, _ in results if v == 'CAUGHT')
print(f'== {caught}/{sum(1 for a in ARMS if a[3])} fault arms caught; null control: '
      + next((v for n, v, _ in results if n.startswith('NULL')), 'not run'))
