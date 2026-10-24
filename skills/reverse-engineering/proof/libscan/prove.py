#!/usr/bin/env python3
"""
Prove libscan.py's markers on real minified builds of every library it names.

  python prove.py build     # install the pinned packages into the cache, bundle every entry twice
  python prove.py suggest   # candidate marker strings per library, to help pick new markers
  python prove.py verify    # run libscan's own scan over every bundle; PASS only on a perfect matrix
  python prove.py verify --write   # also record the run in result.json next to this file
  python prove.py all       # build, then verify --write

Why: a library name is the first thing a minifier throws away, so a scan that
greps for "framer-motion" reports "absent" on a site that ships it (it did, on
example.com). A marker earns its place only by being seen in that
library's own minified build and in no build that lacks the library.

Each file in entries/ is a small, realistic use of one library. Each BUILDS row
bundles one entry, sometimes with an older major version swapped in through a
bundler alias (sites in the wild run old versions), and states which libraries
the bundle really contains. Every row is minified twice, by esbuild and by
webpack in production mode, because the two minify differently. verify scans
all of them with libscan.scan_text and requires that every library is reported
found in exactly the bundles that contain it.

The bundles and node_modules live in the cache directory (--cache, default
~/.cache/re-libproof), never in the repo. Run the build outside the bot's
memory group, it starts webpack:

  systemd-run --user --scope -p MemoryMax=4G python prove.py all
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
DEFAULT_CACHE = Path.home() / ".cache" / "re-libproof"
BUNDLERS = ("esbuild", "webpack")

# name, entry file, bundler aliases, libraries the bundle really contains
BUILDS = [
    ("base-vanilla", "base-vanilla.js", {}, set()),
    ("preact-10", "preact.js", {}, set()),
    ("react-19", "react.js", {}, {"react"}),
    ("react-18", "react.js", {"react": "react18", "react-dom": "react-dom18"}, {"react"}),
    ("vue-3.5", "vue.js", {}, {"vue"}),
    ("framer-motion-14", "framer-motion.js", {}, {"react", "framer-motion"}),
    ("framer-motion-11", "framer-motion.js", {"framer-motion": "framer-motion11"}, {"react", "framer-motion"}),
    ("framer-motion-dom-14", "framer-motion-dom.js", {}, {"framer-motion"}),
    ("framer-motion-dom-11", "framer-motion-dom.js", {"framer-motion": "framer-motion11"}, {"framer-motion"}),
    ("gsap-3.15", "gsap.js", {}, {"gsap"}),
    ("gsap-3.12", "gsap.js", {"gsap": "gsap312"}, {"gsap"}),
    ("scrolltrigger-3.15", "scrolltrigger.js", {}, {"gsap", "gsap-scrolltrigger"}),
    ("scrolltrigger-3.12", "scrolltrigger.js", {"gsap": "gsap312"}, {"gsap", "gsap-scrolltrigger"}),
    ("lenis-1.3", "lenis.js", {}, {"lenis"}),
    ("lenis-1.0-studio-freight", "lenis.js", {"lenis": "@studio-freight/lenis"}, {"lenis"}),
    ("locomotive-5", "locomotive.js", {}, {"locomotive-scroll", "lenis"}),
    ("locomotive-4", "locomotive.js", {"locomotive-scroll": "locomotive4"}, {"locomotive-scroll"}),
    ("three-0.186", "three.js", {}, {"three"}),
    ("three-0.160", "three.js", {"three": "three160"}, {"three"}),
    ("swiper-14", "swiper.js", {}, {"swiper"}),
    ("swiper-11", "swiper.js", {"swiper": "swiper11"}, {"swiper"}),
    ("embla-8", "embla.js", {}, {"embla-carousel"}),
    ("embla-9-rc", "embla.js", {"embla-carousel": "embla9"}, {"embla-carousel"}),
    ("lottie-5.13", "lottie.js", {}, {"lottie-web"}),
    ("anime-4", "anime4.js", {}, {"animejs"}),
    ("anime-3", "anime3.js", {"animejs": "animejs3"}, {"animejs"}),
    ("pixi-8", "pixi8.js", {}, {"pixi.js"}),
    ("pixi-7", "pixi7.js", {"pixi.js": "pixi7"}, {"pixi.js"}),
    ("hls-1.7", "hls.js", {}, {"hls.js"}),
    ("hls-1.5", "hls.js", {"hls.js": "hls15"}, {"hls.js"}),
    ("videojs-8", "videojs.js", {}, {"video.js"}),
    ("videojs-7", "videojs.js", {"video.js": "videojs7"}, {"video.js"}),
    ("videojs-10-html", "videojs10-html.js", {}, {"video.js"}),
    ("videojs-10-react", "videojs10-react.js", {}, {"react", "video.js"}),
    ("plyr-3", "plyr.js", {}, {"plyr"}),
    ("barba-2", "barba.js", {}, {"barba"}),
    ("jquery-4", "jquery.js", {}, {"jquery"}),
    ("jquery-3", "jquery.js", {"jquery": "jquery3"}, {"jquery"}),
]

# The prebuilt minified files each library publishes, the ones a site loads from
# a CDN <script> tag. Several paths are joined into one sample when a page needs
# them together (React's two UMD files).
DIST_FILES = [
    ("gsap-3.15", ["gsap/dist/gsap.min.js"], {"gsap"}),
    ("gsap-3.12", ["gsap312/dist/gsap.min.js"], {"gsap"}),
    ("scrolltrigger-3.15", ["gsap/dist/ScrollTrigger.min.js"], {"gsap-scrolltrigger"}),
    ("scrolltrigger-3.12", ["gsap312/dist/ScrollTrigger.min.js"], {"gsap-scrolltrigger"}),
    ("three-0.160", ["three160/build/three.module.min.js"], {"three"}),
    ("swiper-14", ["swiper/swiper-bundle.min.js"], {"swiper"}),
    ("swiper-11", ["swiper11/swiper-bundle.min.js"], {"swiper"}),
    ("lenis-1.3", ["lenis/dist/lenis.min.js"], {"lenis"}),
    ("lenis-1.0-studio-freight", ["@studio-freight/lenis/dist/lenis.min.js"], {"lenis"}),
    # The UMD file takes Lenis from outside (require("lenis") or a global), it does not carry it.
    ("locomotive-5", ["locomotive-scroll/dist/locomotive-scroll.umd.js"], {"locomotive-scroll"}),
    ("locomotive-4", ["locomotive4/dist/locomotive-scroll.min.js"], {"locomotive-scroll"}),
    ("jquery-4", ["jquery/dist/jquery.min.js"], {"jquery"}),
    ("jquery-3", ["jquery3/dist/jquery.min.js"], {"jquery"}),
    ("anime-4", ["animejs/dist/bundles/anime.umd.min.js"], {"animejs"}),
    ("anime-3", ["animejs3/lib/anime.min.js"], {"animejs"}),
    ("lottie-5.13", ["lottie-web/build/player/lottie.min.js"], {"lottie-web"}),
    ("hls-1.7", ["hls.js/dist/hls.min.js"], {"hls.js"}),
    ("hls-1.5", ["hls15/dist/hls.min.js"], {"hls.js"}),
    ("videojs-8", ["video.js/dist/video.min.js"], {"video.js"}),
    ("videojs-7", ["videojs7/dist/video.min.js"], {"video.js"}),
    ("plyr-3", ["plyr/dist/plyr.min.js"], {"plyr"}),
    ("barba-2", ["@barba/core/dist/barba.umd.js"], {"barba"}),
    ("vue-3.5", ["vue/dist/vue.global.prod.js"], {"vue"}),
    ("react-18", ["react18/umd/react.production.min.js", "react-dom18/umd/react-dom.production.min.js"], {"react"}),
    ("pixi-8", ["pixi.js/dist/pixi.min.js"], {"pixi.js"}),
    ("pixi-7", ["pixi7/dist/pixi.min.js"], {"pixi.js"}),
    ("embla-8", ["embla-carousel/embla-carousel.umd.js"], {"embla-carousel"}),
    ("framer-motion-14", ["framer-motion/dist/framer-motion.js"], {"framer-motion"}),
    ("framer-motion-11", ["framer-motion11/dist/framer-motion.js"], {"framer-motion"}),
]


def samples():
    """Every (name, bundler, expected libraries) the proof scans."""
    out = [(n, b, exp) for n, _, _, exp in BUILDS for b in BUNDLERS]
    out += [(n, "dist", exp) for n, _, exp in DIST_FILES]
    return out


WEBPACK_RUNNER = r"""
const path = require('path');
const webpack = require('webpack');
const jobs = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
(async () => {
  for (const j of jobs) {
    const cfg = {
      mode: 'production', target: 'web', devtool: false, entry: j.entry,
      output: { path: j.outDir, filename: j.out },
      resolve: { alias: j.alias },
      performance: { hints: false },
    };
    const stats = await new Promise((res, rej) => webpack(cfg, (e, s) => (e ? rej(e) : res(s))));
    if (stats.hasErrors()) {
      console.error('FAIL ' + j.out + '\n' + stats.toString({ all: false, errors: true }));
      process.exitCode = 1;
    } else {
      console.log('ok ' + j.out);
    }
  }
})();
"""


def run(cmd, cwd, timeout=900, env=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-4000:])
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd[:4])} ...")
    return proc.stdout


def build(cache):
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", ".npmrc"):
        shutil.copy2(HERE / name, cache / name)
    print("npm install (pinned, install scripts off) ...")
    run(["npm", "install"], cwd=cache)
    entries = cache / "entries"
    if entries.exists():
        shutil.rmtree(entries)
    shutil.copytree(HERE / "entries", entries)
    dist = cache / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir()
    esbuild = str(cache / "node_modules" / ".bin" / "esbuild")
    jobs = []
    for name, entry, alias, _ in BUILDS:
        cmd = [esbuild, f"entries/{entry}", "--bundle", "--minify", "--format=iife", "--platform=browser",
               "--target=es2020", "--define:process.env.NODE_ENV=\"production\"", "--log-level=error",
               f"--outfile=dist/{name}.esbuild.js"]
        cmd += [f"--alias:{k}={v}" for k, v in alias.items()]
        run(cmd, cwd=cache)
        jobs.append({"entry": str(entries / entry), "outDir": str(dist), "out": f"{name}.webpack.js",
                     "alias": alias})
    print(f"esbuild: {len(BUILDS)} bundles")
    (cache / "webpack-jobs.json").write_text(json.dumps(jobs))
    (cache / "webpack-runner.cjs").write_text(WEBPACK_RUNNER)
    out = run(["node", "webpack-runner.cjs", "webpack-jobs.json"], cwd=cache, timeout=1800)
    print(f"webpack: {out.count('ok ')} bundles")
    for name, paths, _ in DIST_FILES:
        text = "\n".join((cache / "node_modules" / rel).read_text(encoding="utf-8") for rel in paths)
        (dist / f"{name}.dist.js").write_text(text, encoding="utf-8")
    print(f"dist: {len(DIST_FILES)} published files")


def bundles(cache):
    dist = cache / "dist"
    out = []
    for name, b, expected in samples():
        p = dist / f"{name}.{b}.js"
        if not p.exists():
            raise SystemExit(f"missing bundle {p}; run: python prove.py build")
        out.append((name, b, p, expected))
    return out


def installed_versions(cache):
    vers = {}
    for dep in json.loads((HERE / "package.json").read_text())["dependencies"]:
        pj = cache / "node_modules" / dep / "package.json"
        if pj.exists():
            meta = json.loads(pj.read_text())
            vers[dep] = f"{meta.get('name')}@{meta.get('version')}"
    return vers


TOKEN_RES = (
    re.compile(r'"((?:[^"\\\n]|\\.){6,80})"'),
    re.compile(r"'((?:[^'\\\n]|\\.){6,80})'"),
    re.compile(r"\.([A-Za-z_$][\w$]{7,40})\b"),
    re.compile(r"[{,]([A-Za-z_$][\w$]{7,40}):"),
)


def tokens(text):
    found = set()
    for rx in TOKEN_RES:
        found.update(m.group(1) for m in rx.finditer(text))
    return found


def suggest(cache, limit, only=None):
    """Strings present in every bundle of a library and in no bundle without it.
    With `only` (build names), the own set is just those builds, which finds the
    markers of one major version when versions share little (anime.js 3 and 4)."""
    toks = {}
    for name, b, p, expected in bundles(cache):
        toks[(name, b)] = tokens(p.read_text(encoding="utf-8", errors="replace"))
    libs = sorted({lib for n, _, exp in samples() for lib in exp if not only or n in only})
    for lib in libs:
        own = [(n, b) for n, b, exp in samples() if lib in exp and (not only or n in only)]
        other = [(n, b) for n, b, exp in samples() if lib not in exp]
        common = set.intersection(*(toks[k] for k in own))
        neg = set().union(*(toks[k] for k in other))
        cands = sorted(common - neg, key=lambda s: (-("-" in s or "_" in s), -len(s)))
        print(f"== {lib}: {len(cands)} candidates in all {len(own)} own bundles, none of {len(other)} others")
        for c in cands[:limit]:
            print(f"   {c}")


def load_libscan():
    import importlib.util
    spec = importlib.util.spec_from_file_location("libscan_under_proof", str(SCRIPTS / "libscan.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verify(cache):
    ls = load_libscan()
    known = {lib["id"] for lib in ls.LIBRARIES}
    rows, failures = [], []
    marker_hits = defaultdict(lambda: defaultdict(list))
    for name, b, p, expected in bundles(cache):
        data = p.read_bytes()
        text = data.decode("utf-8", errors="replace")
        res = ls.scan_text(text)
        found = {lib for lib, r in res.items() if r["status"] == "found"}
        traces = {lib for lib, r in res.items() if r["status"] == "trace"}
        for lib, r in res.items():
            for m in r["markers"]:
                marker_hits[lib][m["marker"]].append(f"{name}.{b}")
        missing = expected - found
        extra = found - expected
        unknown_expected = expected - known
        rows.append({"bundle": f"{name}.{b}", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "expected": sorted(expected), "found": sorted(found), "trace": sorted(traces),
                     "missing": sorted(missing), "extra": sorted(extra)})
        if missing or extra or unknown_expected:
            failures.append(f"{name}.{b}: missing {sorted(missing)} extra {sorted(extra)} "
                            f"unknown {sorted(unknown_expected)}")
        if traces - expected:
            failures.append(f"{name}.{b}: single stray marker for {sorted(traces - expected)}")
    entry_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted((HERE / "entries").glob("*.js")))
    for lib in ls.LIBRARIES:
        for m in lib["markers"]:
            if not marker_hits[lib["id"]].get(m):
                failures.append(f"{lib['id']}: marker {m!r} is in no bundle")
            if m in entry_text:
                failures.append(f"{lib['id']}: marker {m!r} is written in an entry file, so a hit proves nothing")
    return rows, failures, {k: {m: v for m, v in d.items()} for k, d in marker_hits.items()}


def main():
    ap = argparse.ArgumentParser(description="Prove libscan markers on real minified builds.")
    ap.add_argument("cmd", choices=["build", "suggest", "verify", "all"])
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--limit", type=int, default=25, help="Candidates shown per library (suggest).")
    ap.add_argument("--only", nargs="+", help="suggest: only these build names count as the library's own.")
    ap.add_argument("--write", action="store_true", help="verify: write result.json.")
    args = ap.parse_args()
    cache = args.cache.expanduser()
    if args.cmd in ("build", "all"):
        build(cache)
    if args.cmd == "suggest":
        suggest(cache, args.limit, set(args.only) if args.only else None)
        return
    if args.cmd in ("verify", "all"):
        rows, failures, hits = verify(cache)
        n = len(rows)
        print(f"{n} bundles scanned ({len(BUILDS)} entries x {len(BUNDLERS)} bundlers, "
              f"{len(DIST_FILES)} published files)")
        for f in failures:
            print(f"  FAIL {f}")
        verdict = "PASS" if not failures else "FAIL"
        bad = {r["bundle"] for r in rows if r["missing"] or r["extra"]
               or set(r["trace"]) - set(r["expected"])}
        print(f"{verdict}: {n - len(bad)}/{n} bundles exactly right, {len(failures)} problem(s)")
        if args.cmd == "all" or args.write:
            result = {"verdict": verdict, "failures": failures, "packages": installed_versions(cache),
                      "bundlers": {"esbuild": "esbuild --bundle --minify --format=iife --target=es2020",
                                   "webpack": "webpack mode=production (terser)"},
                      "bundles": rows, "marker_hits": hits}
            (HERE / "result.json").write_text(json.dumps(result, indent=1) + "\n")
            print(f"wrote {HERE / 'result.json'}")
        if failures:
            sys.exit(1)


if __name__ == "__main__":
    os.umask(0o022)
    main()
