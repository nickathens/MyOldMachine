"""reverse-engineering skill: watch_page.py tells the page's own clock from an
answer to input.

Ground truth comes from local pages whose behaviour is known exactly: a class
that flips every second on its own, a button that flips another class and fades
an element in, a timer that is set and then cancelled, a tall page whose scroll
handler moves an element, a sticky header, a CSS scroll-driven animation, a
muted autoplay video, and a scroll box in the middle of the screen with a
header in the corner and a sideways rail below. Each test names the fact the
page guarantees and checks the recorder reports it, including the two cases
where the right answer is "nothing happened" (a wheel on a page that ignores
it, a cancelled timer).
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "reverse-engineering" / "scripts"
spec = importlib.util.spec_from_file_location("re_watch_page_under_test", str(SCRIPTS / "watch_page.py"))
wp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wp)

# The page tools need playwright (lazy-imported inside them) and, on recent Ubuntu,
# the AppArmor profile from SKILL.md; where playwright is absent (e.g. CI) these skip.
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

CLOCK_PAGE = """<!doctype html><html><body style="margin:0">
<div id="clock" class="tick">clock</div>
<div id="clicky" class="off">clicky</div>
<div id="fade" style="opacity:0;transition:opacity 300ms linear">fade</div>
<button id="btn" style="width:200px;height:60px">go</button>
<script>
  setInterval(() => document.getElementById('clock').classList.toggle('tick'), 1000);
  const cancelled = setTimeout(() => { document.body.dataset.never = '1'; }, 5000);
  clearTimeout(cancelled);
  document.getElementById('btn').addEventListener('click', () => {
    document.getElementById('clicky').classList.toggle('on');
    document.getElementById('fade').style.opacity = '1';
  });
</script></body></html>"""

SCROLL_PAGE = """<!doctype html><html><head><style>
  body { margin: 0; height: 4000px; }
  #hdr { position: sticky; top: 0; height: 40px; background: #222; }
  @keyframes grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
  #bar { position: fixed; top: 0; left: 0; width: 100%; height: 4px; background: red;
         transform-origin: 0 0; animation: grow linear; animation-timeline: scroll(); }
  #mover { position: absolute; top: 600px; width: 100px; height: 100px; background: #444; }
</style></head><body>
<div id="hdr">header</div><div id="bar"></div><div id="mover"></div>
<script>
  window.addEventListener('scroll', () => {
    document.getElementById('mover').style.transform = 'translateX(' + Math.round(scrollY / 10) + 'px)';
  }, {passive: true});
</script></body></html>"""

# Two class writes in one task, and a tall page whose scrolling is switched off.
BATCH_PAGE = """<!doctype html><html><head><style>html { overflow: hidden; }</style></head>
<body style="margin:0"><div id="b" class="a">x</div><div style="height:3000px"></div>
<script>
  setTimeout(() => { const e = document.getElementById('b'); e.className = 'a b'; e.className = 'a c'; }, 300);
</script></body></html>"""

VIDEO_PAGE = """<!doctype html><html><body style="margin:0">
<video id="v" src="clip.mp4" muted autoplay playsinline style="width:320px"></video>
</body></html>"""

# A header in the top-left corner, a scroll box in the middle of the screen whose
# scroll handler marks it, and a sideways rail that a timer scrolls by 400 px.
CENTRE_PAGE = """<!doctype html><html><head><style>
  body { margin: 0; height: 100vh; overflow: hidden; }
  #hdr { position: absolute; top: 0; left: 0; width: 200px; height: 60px; }
  #box { position: absolute; top: 200px; left: 200px; width: 400px; height: 200px; overflow-y: auto; }
  #box div { height: 1200px; }
  #rail { position: absolute; top: 450px; left: 200px; width: 400px; height: 100px; overflow-x: auto;
          display: flex; scroll-snap-type: x mandatory; }
  #rail div { flex: 0 0 400px; scroll-snap-align: start; }
</style></head><body>
<div id="hdr">header</div><div id="box"><div>tall</div></div><div id="rail"><div>1</div><div>2</div></div>
<script>
  document.getElementById('box').addEventListener('scroll', (e) => e.target.classList.add('moved'), {passive: true});
  setTimeout(() => { document.getElementById('rail').scrollLeft = 400; }, 600);
</script></body></html>"""


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class WatchPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover - the main venv carries it
            raise unittest.SkipTest("playwright is not installed")
        cls.dir = Path(tempfile.mkdtemp(prefix="re-watch-"))
        (cls.dir / "clock.html").write_text(CLOCK_PAGE, encoding="utf-8")
        (cls.dir / "scroll.html").write_text(SCROLL_PAGE, encoding="utf-8")
        (cls.dir / "video.html").write_text(VIDEO_PAGE, encoding="utf-8")
        (cls.dir / "batch.html").write_text(BATCH_PAGE, encoding="utf-8")
        (cls.dir / "centre.html").write_text(CENTRE_PAGE, encoding="utf-8")
        cls.has_video = shutil.which("ffmpeg") is not None
        if cls.has_video:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25", "-t", "3",
                 "-pix_fmt", "yuv420p", "-c:v", "libx264", "-movflags", "+faststart", str(cls.dir / "clip.mp4")],
                check=True, capture_output=True, timeout=60)
        handler = partial(_Quiet, directory=str(cls.dir))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])

        common = dict(url=f"{cls.base}/clock.html", seconds=4.5, viewport={"width": 800, "height": 600},
                      reduced=False, settle_ms=500, max_events=5000, min_delay=250)
        cls.control = wp.run_once(cls.browser, actions=[], **common)
        cls.action_run = wp.run_once(cls.browser, actions=[(1.5, "click", "#btn"), (3.0, "wheel", "300")], **common)
        cls.comparison = {c["action"].split(":")[1]: c for c in wp.compare(cls.action_run, cls.control, 1000)}
        cls.summary = wp.summarize(cls.action_run)

        scroll_common = dict(url=f"{cls.base}/scroll.html", seconds=3.0, viewport={"width": 800, "height": 600},
                             reduced=False, settle_ms=400, max_events=5000, min_delay=250)
        cls.scroll_control = wp.run_once(cls.browser, actions=[], **scroll_common)
        cls.scroll_run = wp.run_once(cls.browser, actions=[(1.0, "scroll", "1500")], **scroll_common)
        cls.scroll_summary = wp.summarize(cls.scroll_run)

        centre_common = dict(url=f"{cls.base}/centre.html", seconds=2.5, viewport={"width": 800, "height": 600},
                             reduced=False, settle_ms=200, max_events=5000, min_delay=250)
        cls.centre_control = wp.run_once(cls.browser, actions=[], **centre_common)
        cls.centre_run = wp.run_once(cls.browser, actions=[(1.0, "wheel", "300")], **centre_common)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_click_is_an_answer_to_input(self):
        c = self.comparison["click"]
        self.assertTrue(c["ok"], c)
        self.assertEqual(c["verdict"], "answered the input")
        self.assertTrue(any(k.startswith("class:div#clicky") for k in c["only_after_action"]), c)

    def test_wheel_on_a_page_that_ignores_it_changes_nothing(self):
        c = self.comparison["wheel"]
        self.assertTrue(c["ok"], c)
        self.assertEqual(c["verdict"], "no change beyond the page's own clock", c)
        self.assertEqual(c["only_after_action"], {})

    def test_the_clock_is_seen_in_the_idle_control_too(self):
        clock = [t for t in wp.summarize(self.control)["class_toggles"] if t["label"].startswith("div#clock")]
        self.assertTrue(clock, "the 1 s class flip must show without any input")
        self.assertGreaterEqual(clock[0]["changes"], 3)
        self.assertAlmostEqual(clock[0]["median_gap_ms"], 1000, delta=150)

    def test_cancelled_timer_is_not_counted_as_fired(self):
        timers = {(t["kind"], t["delay_ms"]): t for t in self.summary["timers"]}
        cancelled = timers[("timeout", 5000)]
        self.assertEqual((cancelled["set"], cancelled["fired"], cancelled["cleared"]), (1, 0, 1))
        clock = timers[("interval", 1000)]
        self.assertEqual(clock["set"], 1)
        self.assertGreaterEqual(clock["fired"], 3)
        self.assertEqual(clock["cleared"], 0)

    def test_transition_reports_property_and_duration(self):
        fades = [t for t in self.summary["transitions_ended"] if t["node"].startswith("div#fade")]
        self.assertTrue(fades, self.summary["transitions_ended"])
        self.assertEqual(fades[0]["property"], "opacity")
        self.assertAlmostEqual(fades[0]["elapsed_s"], 0.3, delta=0.02)

    def test_a_page_that_fits_the_screen_does_not_scroll(self):
        lay = self.summary["layout"]
        self.assertFalse(lay["doc_scrollable"])
        self.assertEqual(lay["scroll_containers"], [])
        self.assertEqual(lay["scroll_timelines"], [])

    def test_scroll_is_an_answer_to_input_on_a_scroll_page(self):
        (c,) = wp.compare(self.scroll_run, self.scroll_control, 1000)
        self.assertEqual(c["verdict"], "answered the input", c)
        self.assertTrue(any(k.startswith("style:div#mover") for k in c["only_after_action"]), c)
        self.assertGreater(self.scroll_summary["scroll_events"], 0)

    def test_scroll_layout_flags(self):
        lay = self.scroll_summary["layout"]
        self.assertTrue(lay["doc_scrollable"])
        labels = {self.scroll_run["nodes"][str(n)]["label"] for n in lay["sticky"]}
        self.assertIn("div#hdr", labels)
        timelines = [t for t in lay["scroll_timelines"]
                     if self.scroll_run["nodes"][str(t["node"])]["label"] == "div#bar"]
        self.assertTrue(timelines, lay["scroll_timelines"])
        self.assertIn("scroll", timelines[0]["timeline"])

    def test_wheel_reaches_the_middle_of_the_screen(self):
        # Fired at the page's top-left corner, the wheel lands on the header and the box never scrolls.
        (c,) = wp.compare(self.centre_run, self.centre_control, 1000)
        self.assertEqual(c["verdict"], "answered the input", c)
        self.assertIn("scroll:div#box", c["only_after_action"])
        self.assertTrue(c["received_by"] and "div#hdr" not in c["received_by"], c)

    def test_a_sideways_scroller_is_listed_with_its_axis(self):
        run = self.centre_control
        axes = {run["nodes"][str(s["node"])]["label"]: s["axis"] for s in run["layout"]["scroll_containers"]}
        self.assertEqual(axes, {"div#box": "y", "div#rail": "x"})
        rail = [r for r in run["log"] if r["k"] == "scroll" and wp.node_label(run, r["node"]) == "div#rail"]
        self.assertTrue(rail, run["log"])
        self.assertEqual((rail[-1]["left"], rail[-1]["top"]), (400, 0))

    def test_video_play_is_logged_with_its_source(self):
        if not self.has_video:
            self.skipTest("ffmpeg is not installed")
        run = wp.run_once(self.browser, url=f"{self.base}/video.html", seconds=1.5,
                          viewport={"width": 800, "height": 600}, reduced=False, actions=[], settle_ms=300,
                          max_events=5000, min_delay=250)
        plays = wp.summarize(run)["media_plays"]
        self.assertTrue(plays, run["log"][:20])
        self.assertEqual(plays[0]["src"], "clip.mp4")

    def test_two_writes_in_one_batch_are_told_apart(self):
        run = wp.run_once(self.browser, url=f"{self.base}/batch.html", seconds=1.2,
                          viewport={"width": 800, "height": 600}, reduced=False, actions=[], settle_ms=100,
                          max_events=5000, min_delay=250)
        changes = [(r["added"], r["removed"]) for r in run["log"]
                   if r["k"] == "class" and wp.node_label(run, r["node"]) == "div#b.a"]
        self.assertEqual(changes, [(["b"], []), (["c"], ["b"])])
        self.assertFalse(run["layout"]["doc_scrollable"])
        self.assertEqual(run["layout"]["viewport_overflow_y"], "hidden")
        self.assertGreater(run["layout"]["doc_scroll_height"], 2000)

    def test_reduced_motion_is_emulated(self):
        page_js = "matchMedia('(prefers-reduced-motion: reduce)').matches"
        ctx = self.browser.new_context(reduced_motion="reduce")
        try:
            page = ctx.new_page()
            page.goto(f"{self.base}/clock.html")
            self.assertTrue(page.evaluate(page_js))
        finally:
            ctx.close()


def _fake_run(start_ms, records, actions=()):
    nodes = {"1": {"label": "div.caption", "parents": []}, "2": {"label": "div.slide", "parents": []}}
    return {"start_ms": start_ms, "end_ms": start_ms + 20000, "nodes": nodes, "log": list(records),
            "actions": list(actions), "raf": {}, "dropped": {}, "layout": {}}


class CompareLogic(unittest.TestCase):
    """compare() without a browser: the per-frame jitter case found on
    example.com (an animation running in both runs, a few frames apart, is
    not an answer to a key press), a change absent from the idle run (is one),
    and a change far beyond jitter on a shared node (is one)."""

    ACTION = {"t": 2.0, "kind": "key", "arg": "ArrowLeft", "page_ms": 5000, "ok": True, "error": None}

    def frames(self, start, n, node=1):
        return [{"k": "style", "node": node, "value": "x", "ms": start + 16 * i} for i in range(n)]

    def test_a_few_more_animation_frames_is_not_an_answer(self):
        control = _fake_run(3000, self.frames(5100, 20))
        action = _fake_run(3000, self.frames(5100, 22), [self.ACTION])
        (c,) = wp.compare(action, control, 1500)
        self.assertEqual(c["verdict"], "no change beyond the page's own clock", c)

    def test_a_change_absent_from_the_idle_run_is_an_answer(self):
        control = _fake_run(3000, self.frames(5100, 20))
        action = _fake_run(3000, self.frames(5100, 20) + [{"k": "class", "node": 2, "added": ["on"],
                                                           "removed": [], "ms": 5200}], [self.ACTION])
        (c,) = wp.compare(action, control, 1500)
        self.assertEqual(c["verdict"], "answered the input")
        self.assertEqual(c["only_after_action"], {"class:div.slide": 1})

    def test_far_more_of_a_shared_change_is_an_answer(self):
        control = _fake_run(3000, self.frames(5100, 4))
        action = _fake_run(3000, self.frames(5100, 30), [self.ACTION])
        (c,) = wp.compare(action, control, 1500)
        self.assertEqual(c["verdict"], "answered the input")
        self.assertEqual(c["far_more_after_action"], {"style:div.caption": 26})

    def test_a_failed_action_gets_no_verdict(self):
        # A click whose selector was never found did not happen; "no change" would read as "clicking does nothing".
        failed = dict(self.ACTION, kind="click", arg="#missing", ok=False, error="Timeout 5000ms exceeded.")
        control = _fake_run(3000, self.frames(5100, 20))
        action = _fake_run(3000, self.frames(5100, 20), [failed])
        (c,) = wp.compare(action, control, 1500)
        self.assertEqual(c["verdict"], "action failed: no verdict", c)

    def test_runs_are_aligned_on_their_settle_points(self):
        # Same page, the control simply settled 700 ms later on its own clock.
        control = _fake_run(3700, [{"k": "class", "node": 2, "added": ["a"], "removed": [], "ms": 5900}])
        action = _fake_run(3000, [{"k": "class", "node": 2, "added": ["a"], "removed": [], "ms": 5200}],
                           [self.ACTION])
        (c,) = wp.compare(action, control, 1500)
        self.assertEqual(c["verdict"], "no change beyond the page's own clock", c)


class OlderRecordings(unittest.TestCase):
    def test_timer_records_without_an_event_are_counted_apart(self):
        # An earlier recorder logged timers with no set/fire/clear field (four of the kept Supreme runs).
        run = _fake_run(3000, [{"k": "timer", "kind": "timeout", "delay": 10000, "ms": 4000},
                               {"k": "timer", "ev": "set", "kind": "timeout", "delay": 1000, "id": 7, "ms": 4100}])
        s = wp.summarize(run)
        self.assertEqual(s["timers"], [{"kind": "timeout", "delay_ms": 1000, "set": 1, "fired": 0, "cleared": 0}])
        self.assertEqual(s["timers_unlabelled"], 1)


class ActionParsing(unittest.TestCase):
    def test_good_specs(self):
        self.assertEqual(wp.parse_action("3:wheel:600"), (3.0, "wheel", "600"))
        self.assertEqual(wp.parse_action("1.5:click:button[aria-label='Next: slide']"),
                         (1.5, "click", "button[aria-label='Next: slide']"))
        self.assertEqual(wp.parse_action("2:key:ArrowRight"), (2.0, "key", "ArrowRight"))

    def test_bad_specs_are_refused(self):
        for bad in ("wheel", "x:wheel:1", "-1:wheel:1", "2:jump:1", "2:click", "2:wheel:far"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    wp.parse_action(bad)


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed")
class PageToolsRunInChromiumsSandbox(unittest.TestCase):
    """capture_net and watch_page run a page's JavaScript, so their Chromium must
    keep its own sandbox: read off the running processes, not the launch call."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "chromium_sandbox_probe", str(Path(__file__).resolve().parent / "chromium_sandbox_probe.py"))
        cls.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.probe)
        cls.dir = Path(tempfile.mkdtemp(prefix="re-sandboxed-"))
        (cls.dir / "p.html").write_text("<!doctype html><p>sandboxed</p>", encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(cls.dir)))
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/p.html"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _run(self, argv):
        proc = subprocess.Popen([sys.executable, *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            tree = self.probe.wait_for_renderer(proc.pid, timeout=30)
            _, err = proc.communicate(timeout=60)
        finally:
            proc.kill()
        self.assertEqual(proc.returncode, 0, err.decode(errors="replace")[-600:])
        self.assertEqual(self.probe.sandbox_faults(tree), [], tree)

    def test_capture_net(self):
        self._run([str(SCRIPTS / "capture_net.py"), "--url", self.url, "--wait-ms", "3000",
                   "--out", str(self.dir / "net.json")])

    def test_watch_page(self):
        self._run([str(SCRIPTS / "watch_page.py"), "--url", self.url, "--seconds", "3", "--settle-ms", "200"])


if __name__ == "__main__":
    unittest.main()
