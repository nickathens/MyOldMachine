#!/usr/bin/env python3
"""Headless web app verification via Playwright.

Loads a URL with a fresh Chromium context, runs a list of assertions,
and exits 0 if all pass. Use after rendering a presentation, deploying
a static site, or shipping a web component to verify it actually works.

Assertions are declared in a YAML/JSON manifest or via CLI flags.

Manifest example (YAML):

    url: https://example.com/treatment
    timeout: 30
    viewport: [1920, 1080]
    checks:
      - http_status: 200
      - no_console_errors: true
      - no_failed_requests: true
      - selector_exists: "section.hero"
      - selector_count: ["section[data-anim]", 12]
      - selector_text_contains: ["h1", "Treatment"]
      - no_text: "undefined"
      - no_text: "Lorem ipsum"
      - eval_truthy: "document.fonts.check('1em Montserrat')"
      - max_load_time_ms: 5000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

from playwright.async_api import async_playwright

CheckResult = tuple[bool, str]


class AssertionRun:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.results: list[CheckResult] = []
        self.console_errors: list[str] = []
        self.failed_requests: list[str] = []

    async def run(self) -> bool:
        url = self.manifest["url"]
        timeout = self.manifest.get("timeout", 30) * 1000
        viewport = tuple(self.manifest.get("viewport", [1280, 800]))
        checks = self.manifest.get("checks", [])

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--no-sandbox"])
            context = await browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]}
            )
            page = await context.new_page()

            page.on("console", self._capture_console)
            page.on("requestfailed", self._capture_request_failed)
            page.on("response", self._capture_bad_response)

            try:
                try:
                    response = await page.goto(
                        url, wait_until="networkidle", timeout=timeout
                    )
                    load_status = response.status if response else 0
                except Exception as exc:  # noqa: BLE001
                    self.results.append((False, f"navigation failed: {exc}"))
                    return False

                for check in checks:
                    try:
                        ok, msg = await self._run_check(
                            page, check, load_status
                        )
                    except Exception as exc:  # noqa: BLE001
                        ok, msg = False, f"check raised: {check} -> {exc}"
                    self.results.append((ok, msg))
            finally:
                await browser.close()
        return all(ok for ok, _ in self.results)

    def _capture_console(self, msg: Any) -> None:
        if msg.type == "error":
            self.console_errors.append(msg.text)

    def _capture_request_failed(self, request: Any) -> None:
        self.failed_requests.append(f"{request.method} {request.url}")

    def _capture_bad_response(self, response: Any) -> None:
        if response.status >= 400:
            self.failed_requests.append(
                f"{response.status} {response.request.method} {response.url}"
            )

    async def _run_check(
        self, page: Any, check: dict[str, Any], load_status: int
    ) -> CheckResult:
        if not isinstance(check, dict) or len(check) != 1:
            return False, f"malformed check: {check!r}"
        name, value = next(iter(check.items()))

        if name == "http_status":
            return (
                load_status == value,
                f"http_status={load_status} expected={value}",
            )
        if name == "no_console_errors":
            if value and self.console_errors:
                return False, f"console errors: {self.console_errors}"
            return True, "no console errors"
        if name == "no_failed_requests":
            if value and self.failed_requests:
                return False, f"failed requests: {self.failed_requests}"
            return True, "no failed requests"
        if name == "selector_exists":
            count = await page.locator(value).count()
            return count > 0, f"selector_exists {value!r}: count={count}"
        if name == "selector_count":
            if not (isinstance(value, (list, tuple)) and len(value) == 2):
                return False, f"selector_count needs [selector, n], got {value!r}"
            sel, expected = value
            count = await page.locator(sel).count()
            return (
                count == expected,
                f"selector_count {sel!r}: count={count} expected={expected}",
            )
        if name == "selector_text_contains":
            if not (isinstance(value, (list, tuple)) and len(value) == 2):
                return (
                    False,
                    f"selector_text_contains needs [selector, text], got {value!r}",
                )
            sel, needle = value
            text = await page.locator(sel).first.text_content()
            present = needle in (text or "")
            return present, f"text in {sel!r}: present={present}"
        if name == "no_text":
            body = await page.content()
            present = value in body
            return not present, f"no_text {value!r}: present={present}"
        if name == "eval_truthy":
            result = await page.evaluate(f"() => Boolean({value})")
            return bool(result), f"eval_truthy {value!r}: {result}"
        if name == "max_load_time_ms":
            timing = await page.evaluate(
                "() => { const t = performance.timing; "
                "return t.loadEventEnd - t.navigationStart; }"
            )
            return (
                timing <= value,
                f"load_time_ms={timing} max={value}",
            )
        return False, f"unknown check: {name}"


def load_manifest(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        if yaml is None:
            print(
                "PyYAML missing; pip install pyyaml or use JSON manifest.",
                file=sys.stderr,
            )
            sys.exit(2)
        return yaml.safe_load(text)
    return json.loads(text)


def manifest_from_args(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if args.no_console_errors:
        checks.append({"no_console_errors": True})
    if args.no_failed_requests:
        checks.append({"no_failed_requests": True})
    for sel in args.selector_exists or []:
        checks.append({"selector_exists": sel})
    for needle in args.no_text or []:
        checks.append({"no_text": needle})
    for status in (args.http_status,) if args.http_status else ():
        checks.append({"http_status": status})
    return {
        "url": args.url,
        "timeout": args.timeout,
        "viewport": [args.viewport_width, args.viewport_height],
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run assertions against a web page and exit non-zero on failure."
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-m", "--manifest", help="Path to YAML/JSON manifest.")
    g.add_argument("--url", help="URL to assert against (use with --check flags).")
    p.add_argument("--timeout", type=int, default=30, help="Navigation timeout (s).")
    p.add_argument("--viewport-width", type=int, default=1280)
    p.add_argument("--viewport-height", type=int, default=800)
    p.add_argument("--http-status", type=int, help="Expected HTTP status.")
    p.add_argument("--no-console-errors", action="store_true")
    p.add_argument("--no-failed-requests", action="store_true")
    p.add_argument(
        "--selector-exists",
        action="append",
        help="Assert this CSS selector matches at least one element.",
    )
    p.add_argument(
        "--no-text",
        action="append",
        help="Assert this string does NOT appear in the rendered page.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable lines.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    manifest = (
        load_manifest(Path(args.manifest)) if args.manifest else manifest_from_args(args)
    )
    if not manifest.get("checks"):
        print("No checks declared.", file=sys.stderr)
        return 2

    run = AssertionRun(manifest)
    ok = asyncio.run(run.run())

    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "results": [
                        {"ok": r[0], "msg": r[1]} for r in run.results
                    ],
                    "console_errors": run.console_errors,
                    "failed_requests": run.failed_requests,
                },
                indent=2,
            )
        )
    else:
        for r_ok, msg in run.results:
            mark = "PASS" if r_ok else "FAIL"
            print(f"[{mark}] {msg}")
        print()
        print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
