"""The Mini App's image and video menu is the image tool's own list.

The page carried a hand-typed copy of the models, their durations, ratios,
resolutions and reference-image support, and the server a copy of every model's
ratios. Both drifted from skills/image-gen/scripts/generate.py: the five video
models it gained on 2026-08-07 never got a button, Soul Cast stayed on the video
list after it became a still model, and a new model was offered ratios it
refuses. These pin the one list (MODEL_MENU and menu()), and the two settings
the tool used to drop on the way to Higgsfield. Offline: subprocess.run is
replaced, nothing reaches Higgsfield.
"""

import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "image-gen" / "scripts" / "generate.py"
PAGE = ROOT / "miniapp" / "static" / "index.html"


def load():
    spec = importlib.util.spec_from_file_location("image_gen_generate_menu", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = load()

KINDS = {"image": (gen.MODEL_ALIASES, gen.DEFAULT_MODEL), "video": (gen.VIDEO_MODEL_ALIASES, gen.DEFAULT_VIDEO_MODEL)}


class MenuIsTheToolsList(unittest.TestCase):
    def setUp(self):
        self.menu = gen.menu()

    def test_every_aliased_model_has_a_row(self):
        # A model aliased in the tool and missing here would never reach the menu.
        aliased = set(gen.MODEL_ALIASES.values()) | set(gen.VIDEO_MODEL_ALIASES.values())
        self.assertEqual(sorted(aliased - set(gen.MODEL_MENU)), [])

    def test_no_row_without_an_alias(self):
        aliased = set(gen.MODEL_ALIASES.values()) | set(gen.VIDEO_MODEL_ALIASES.values())
        self.assertEqual(sorted(set(gen.MODEL_MENU) - aliased), [])

    def test_one_card_per_model_and_each_card_resolves_to_it(self):
        for kind, (aliases, _) in KINDS.items():
            with self.subTest(kind=kind):
                cards = self.menu[kind]
                ids = [c["id"] for c in cards]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(sorted(gen.resolve_model(i, kind) for i in ids), sorted(set(aliases.values())))

    def test_the_tools_default_is_the_one_default(self):
        for kind, (_, default) in KINDS.items():
            with self.subTest(kind=kind):
                defaults = [c["id"] for c in self.menu[kind] if c.get("default")]
                self.assertEqual(len(defaults), 1)
                self.assertEqual(gen.resolve_model(defaults[0], kind), default)

    def test_the_five_video_models_have_cards(self):
        ids = {c["id"] for c in self.menu["video"]}
        for alias in ("h3", "flux-video", "grok-video1.5", "happy-horse", "seedance2.5"):
            self.assertIn(alias, ids)

    def test_soul_cast_is_a_still_model_on_the_menu(self):
        self.assertIn("soul-cast", {c["id"] for c in self.menu["image"]})
        self.assertNotIn("soul-cast", {c["id"] for c in self.menu["video"]})

    def test_a_model_with_two_aliases_shows_once_under_the_first(self):
        ids = [c["id"] for c in self.menu["video"]]
        self.assertIn("h3", ids)
        self.assertNotIn("hailuo3", ids)
        self.assertIn("flux-video", ids)
        self.assertNotIn("flux3-video", ids)


class CardsOfferOnlyWhatTheModelTakes(unittest.TestCase):
    """Values read off `higgsfield model get` and `generate cost`, CLI 1.1.26, 2026-10-06."""

    def setUp(self):
        self.cards = {(kind, c["id"]): c for kind in ("image", "video") for c in gen.menu()[kind]}

    def card(self, kind, alias):
        return self.cards[(kind, alias)]

    def test_ratios_are_ones_the_server_accepts(self):
        for (kind, alias), card in self.cards.items():
            with self.subTest(model=alias, kind=kind):
                self.assertTrue(set(card["ratios"]) <= set(gen.ASPECT_RATIOS))

    def test_soul_cast_takes_16_9_only(self):
        # The server's old table offered it all eleven.
        self.assertEqual(self.card("image", "soul-cast")["ratios"], ["16:9"])

    def test_models_without_a_ratio_get_no_ratio_row(self):
        for alias in ("hailuo", "grok-video1.5"):
            self.assertEqual(self.card("video", alias)["ratios"], [])
        self.assertEqual(gen.NO_ASPECT_RATIO_MODELS, {"minimax_hailuo", "grok_video_v15"})

    def test_reference_image_support(self):
        # "Model does not accept media inputs" for these four
        for alias in ("soul-location", "z", "recraft", "soul-cast"):
            self.assertIsNone(self.card("image", alias)["ref"], alias)
        self.assertEqual(self.card("video", "veo3")["ref"], "required")
        # the page offered no slot to these, though each takes one
        for kind, alias in (("image", "nano-lite"), ("image", "soul-cinema"), ("video", "kling-turbo"),
                            ("video", "gemini"), ("video", "cinematic3.5"), ("video", "seedance-mini")):
            self.assertEqual(self.card(kind, alias)["ref"], "optional", alias)

    def test_resolutions(self):
        self.assertEqual(self.card("image", "nano2")["resolutions"], ["1k", "2k", "4k"])
        self.assertEqual(self.card("image", "nano-lite")["resolutions"], ["1k"])
        self.assertEqual(self.card("image", "soul")["resolutions"], [])
        # the page offered Grok and MS Image no resolution at all
        self.assertEqual(self.card("image", "grok")["resolutions"], ["1k", "2k"])
        self.assertEqual(self.card("image", "ms")["resolutions"], ["1k", "2k", "4k"])

    def test_durations_are_the_tools(self):
        # The page's own copy said 5-30 for both Seedance 2.0 models (the
        # validator refuses 16 up and allows 4), 3-15 for Wan 2.7 (2 is fine)
        # and 3-10 for Cinematic V2 (12 is fine).
        for alias, job in (("seedance", "seedance_2_0"), ("seedance-mini", "seedance_2_0_mini"),
                           ("wan", "wan2_7"), ("cinematic-v2", "cinematic_studio_video_v2")):
            self.assertEqual(self.card("video", alias)["duration"], gen.VIDEO_DURATIONS[job], alias)
        self.assertIsNone(self.card("video", "veo3")["duration"])

    def test_no_option_without_a_default(self):
        # The page preselects every option it shows and sends it. seedance_2_5's
        # extension_mode has no default and is refused outside mode
        # video_extension, so showing it broke every Seedance 2.5 request.
        for (kind, alias), card in self.cards.items():
            for key, spec in card["options"].items():
                self.assertIsNotNone(spec.get("default"), f"{alias}: {key}")
        self.assertNotIn("extension_mode", self.card("video", "seedance2.5")["options"])

    def test_an_images_resolution_is_not_also_an_option(self):
        # recraft carries one in MODEL_PARAMS; drawn twice it went out twice.
        for (kind, alias), card in self.cards.items():
            if kind == "image":
                self.assertNotIn("resolution", card["options"], alias)

    def test_flux_2_sends_variant(self):
        # "Unknown params: model", the default the FLUX 2 card sent on every request
        self.assertIn("variant", self.card("image", "flux")["options"])
        self.assertNotIn("model", self.card("image", "flux")["options"])

    def test_prices(self):
        for (kind, alias), card in self.cards.items():
            lo, hi = gen.MODEL_MENU[gen.resolve_model(alias, kind)]["credits"]
            self.assertTrue(0 < lo <= hi, alias)
        self.assertEqual(self.card("image", "nano2")["cost"], "1.5-3 cr")
        self.assertEqual(self.card("image", "soul")["cost"], "0.12 cr")
        self.assertEqual(self.card("video", "kling")["cost"], "1.5-6 cr/s")
        # priced per clip: veo3 has no duration
        self.assertEqual(self.card("video", "veo3")["cost"], "22-58 cr")


class ChosenSettingsReachHiggsfield(unittest.TestCase):
    """A value somebody picked is sent; one nobody picked leaves the model's own."""

    def setUp(self):
        self.gen = load()
        self.calls = []

        def fake_run(cmd, **kwargs):
            self.calls.append(cmd)
            return mock.Mock(returncode=1, stdout="", stderr="stop here")

        patcher = mock.patch.object(self.gen.subprocess, "run", side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        acct = mock.patch.object(self.gen, "get_account_status", return_value={"credits": 0})
        acct.start()
        self.addCleanup(acct.stop)

    @staticmethod
    def flag(cmd, name):
        return cmd[cmd.index(name) + 1] if name in cmd else None

    def test_2k_reaches_a_model_whose_default_is_1k(self):
        # nano_banana_flash quotes 1.5 with no resolution, the same as 1k, and 2
        # at 2k: the 2K the page preselects bought a 1K picture.
        self.gen.generate_higgsfield("a cat", "/tmp/x.jpg", model="nano2", resolution="2k")
        self.assertEqual(self.flag(self.calls[-1], "--resolution"), "2k")
        self.gen.estimate_cost("nano2", "a cat", resolution="2k", kind="image")
        self.assertEqual(self.flag(self.calls[-1], "--resolution"), "2k")

    def test_no_resolution_to_a_model_without_one(self):
        # "Unknown params: resolution": a soul job asked for 4k failed outright
        self.gen.generate_higgsfield("a cat", "/tmp/x.jpg", model="soul", resolution="4k")
        self.assertNotIn("--resolution", self.calls[-1])
        self.gen.estimate_cost("soul", "a cat", resolution="4k", kind="image")
        self.assertNotIn("--resolution", self.calls[-1])

    def test_a_square_reaches_soul_cinema(self):
        # soul_cinema_studio defaults to 16:9, and 1:1 was never sent
        self.gen.generate_higgsfield("a face", "/tmp/x.jpg", model="soul-cinema", aspect_ratio="1:1")
        self.assertEqual(self.flag(self.calls[-1], "--aspect_ratio"), "1:1")

    def test_landscape_reaches_the_video_models_that_default_elsewhere(self):
        # marketing_studio_video defaults to 9:16; h3, flux-video and
        # cinematic3.5 to auto. 16:9 was never sent to any of them.
        for alias in ("marketing", "h3", "flux-video", "cinematic3.5"):
            with self.subTest(model=alias):
                self.gen.generate_video("a shot", "/tmp/x.mp4", model=alias, aspect_ratio="16:9")
                self.assertEqual(self.flag(self.calls[-1], "--aspect_ratio"), "16:9")

    def test_the_quote_sends_what_the_job_sends(self):
        cases = (("image", "soul-cinema", "1:1", None), ("image", "nano2", "16:9", "2k"),
                 ("video", "marketing", "16:9", None), ("video", "hailuo", "16:9", None))
        for kind, alias, ratio, res in cases:
            with self.subTest(model=alias):
                if kind == "image":
                    self.gen.generate_higgsfield("p", "/tmp/x.jpg", model=alias, aspect_ratio=ratio, resolution=res)
                else:
                    self.gen.generate_video("p", "/tmp/x.mp4", model=alias, aspect_ratio=ratio)
                job = self.calls[-1]
                self.gen.estimate_cost(alias, "p", aspect_ratio=ratio, resolution=res, kind=kind)
                quote = self.calls[-1]
                for name in ("--aspect_ratio", "--resolution"):
                    self.assertEqual(self.flag(job, name), self.flag(quote, name), name)

    def run_main(self, *argv):
        with mock.patch("sys.argv", ["generate.py", *argv]), mock.patch("builtins.print"):
            with self.assertRaises(SystemExit):
                self.gen.main()
        return [c for c in self.calls if c[:3] == ["higgsfield", "generate", "create"]][-1]

    def test_nothing_chosen_sends_nothing(self):
        # Chat use without -a or --resolution sends what it sent before: the
        # model's own ratio and resolution.
        cmd = self.run_main("a cat", "-m", "nano2", "--backend", "higgsfield")
        self.assertNotIn("--aspect_ratio", cmd)
        self.assertNotIn("--resolution", cmd)
        cmd = self.run_main("a shot", "--video", "-m", "marketing")
        self.assertNotIn("--aspect_ratio", cmd)


class PageKeepsNoListOfItsOwn(unittest.TestCase):
    """index.html: the menu comes from /api/media/menu, nothing hand-typed."""

    def setUp(self):
        self.page = PAGE.read_text(encoding="utf-8")

    def test_no_hand_typed_tables(self):
        for name in ("IMAGE_MODELS", "VIDEO_MODELS", "MODEL_DURATIONS", "MODEL_INPUTS", "MODEL_RESOLUTIONS"):
            self.assertNotIn(name, self.page)
        # no card literal such as {id:'nano2',name:'Nano 2',...}
        self.assertIsNone(re.search(r"\{id:'[a-z0-9.\-]+',name:", self.page))

    def test_menu_is_fetched_from_the_tool(self):
        self.assertIn("/api/media/menu", self.page)
        self.assertNotIn("/api/media/model-ratios", self.page)

    def test_no_null_ratio_in_the_request(self):
        # aspect_ratio:mgState.aspect sent null for Hailuo and the server refused it
        self.assertNotIn("aspect_ratio:mgState.aspect", self.page)


class BotHandOffNamesOnlyWhatWasSent(unittest.TestCase):
    """bot.py builds the turn from the page's request. A ratio or resolution
    the page did not send must not be invented there: it read 1:1 and 2k into
    every Hailuo and Soul request and put them in the cost command."""

    def setUp(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.index('pending_mg = Path(f"/tmp/media_gen_pending_{user_id}.json")')
        self.block = src[start:src.index("user_message = mg_context", start)]

    def test_no_invented_defaults(self):
        self.assertNotIn('mg.get("aspect_ratio", "1:1")', self.block)
        self.assertNotIn('mg.get("resolution", "2k")', self.block)
        self.assertNotIn("--cost -m {mg_model} -a {mg_aspect}", self.block)

    def test_every_card_has_a_prompt_guide(self):
        # A card with no route falls back to nano-banana.md, a still guide.
        block = re.search(r"_MODEL_GUIDES = \{(.*?)\n\s*\}", self.block, re.S)
        self.assertIsNotNone(block)
        routed = set(re.findall(r'"([a-z0-9.\-]+)":\s*(?:"|\()', block.group(1)))
        guides = set(re.findall(r'"([a-z0-9\-]+\.md)"', block.group(1)))
        menu = gen.menu()
        missing = sorted(c["id"] for kind in ("image", "video") for c in menu[kind] if c["id"] not in routed)
        self.assertEqual(missing, [])
        for guide in guides:
            self.assertTrue((SCRIPT.parent.parent / "models" / guide).is_file(), guide)


@unittest.skipUnless(importlib.util.find_spec("playwright"), "needs Playwright with Chromium")
class LivePage(unittest.TestCase):
    """index.html in headless Chromium, its API answered from the real menu().

    The source checks above cannot see what the page sends; this clicks through
    the cards that were broken and reads the request off the wire.
    """

    TG_STUB = ("window.Telegram={WebApp:{initData:'test',initDataUnsafe:{user:{id:1}},"
               "ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},close(){},"
               "HapticFeedback:{impactOccurred(){},selectionChanged(){},notificationOccurred(){}}}};")

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.menu = gen.menu()
        self.page = self.browser.new_page()
        self.page.set_default_timeout(10000)
        self.addCleanup(self.page.close)
        self.page.route("**/*", self.answer)
        self.page.goto("http://mom.test/app")
        self.page.click('button.tab[data-view="launch"]')
        self.page.click('.launch-card[data-skill="media-gen"]')
        self.page.wait_for_selector("#mg-models .mg-model-card")

    def answer(self, route):
        url = route.request.url
        if url.startswith("https://telegram.org/"):
            return route.fulfill(content_type="text/javascript", body=self.TG_STUB)
        if "://mom.test" not in url:
            return route.abort()
        path = url.split("://mom.test", 1)[1].split("?")[0]
        if path in ("/", "/app"):
            return route.fulfill(content_type="text/html", body=PAGE.read_text(encoding="utf-8"))
        if path == "/api/media/menu":
            return route.fulfill(content_type="application/json", body=json.dumps(self.menu))
        if path == "/api/launch":
            return route.fulfill(content_type="application/json", body='{"ok": true}')
        return route.fulfill(content_type="application/json", body="{}")

    def names(self):
        return self.page.eval_on_selector_all("#mg-models .mg-model-name", "els => els.map(e => e.textContent)")

    def pick(self, kind, name):
        if kind == "video":
            self.page.click('#mg-type-selector button:text-is("Video")')
        self.page.click(f'#mg-models .mg-model-card:has(.mg-model-name:text-is("{name}"))')

    def send(self):
        self.page.fill("#mg-prompt", "a test")
        with self.page.expect_request("**/api/launch") as req:
            self.page.click("#mg-generate")
        return json.loads(req.value.post_data)["config"]

    def test_every_card_is_drawn_in_the_tools_order(self):
        self.assertEqual(self.names(), [c["name"] for c in self.menu["image"]])
        self.page.click('#mg-type-selector button:text-is("Video")')
        self.assertEqual(self.names(), [c["name"] for c in self.menu["video"]])

    def test_hailuo_sends_no_ratio(self):
        self.pick("video", "Hailuo")
        self.assertFalse(self.page.is_visible("#mg-aspect"))
        config = self.send()
        self.assertEqual(config["model"], "hailuo")
        self.assertNotIn("aspect_ratio", config)
        self.assertEqual(config["duration"], 6)

    def test_a_video_keeps_16_9_after_a_model_with_no_ratio(self):
        self.pick("video", "Hailuo")
        self.page.click('#mg-models .mg-model-card:has(.mg-model-name:text-is("Kling 3.0"))')
        self.assertEqual(self.send()["aspect_ratio"], "16:9")

    def test_soul_cast_offers_16_9_and_no_reference(self):
        self.pick("image", "Soul Cast")
        pills = self.page.eval_on_selector_all("#mg-aspect .mg-pill", "els => els.map(e => e.textContent)")
        self.assertEqual(pills, ["16:9"])
        self.assertFalse(self.page.is_visible("#mg-attach-section"))
        self.assertEqual(self.send()["aspect_ratio"], "16:9")

    def test_veo3_holds_generate_until_a_reference(self):
        self.pick("video", "Veo 3")
        self.page.fill("#mg-prompt", "a test")
        self.assertTrue(self.page.is_visible("#mg-attach-req"))
        self.assertTrue(self.page.is_disabled("#mg-generate"))

    def test_seedance_2_5_sends_no_extension_mode(self):
        self.pick("video", "Seedance 2.5")
        extra = self.send()["extra_params"]
        self.assertEqual(extra["mode"], "t2v")
        self.assertNotIn("extension_mode", extra)

    def test_nano2_sends_the_2k_it_shows(self):
        selected = self.page.text_content("#mg-res .mg-pill.selected")
        self.assertEqual(selected, "2K")
        config = self.send()
        self.assertEqual((config["model"], config["resolution"]), ("nano2", "2k"))


class MenuCommand(unittest.TestCase):
    def test_menu_flag_prints_the_menu(self):
        printed = []
        with mock.patch("sys.argv", ["generate.py", "--menu"]), \
                mock.patch("builtins.print", side_effect=lambda s, **k: printed.append(s)):
            gen.main()
        self.assertEqual(json.loads(printed[0]), json.loads(json.dumps(gen.menu())))


if __name__ == "__main__":
    unittest.main()
