"""Planted-fault battery for tests/test_upscale_magnific.py (scripts/magnific.py).

Each arm plants one fault in a COPY of magnific.py in a temp tree (the real
file is never touched) and runs the test file against it. Verdicts:
  CAUGHT    the named test failed on an assertion
  ERRORED   the named test failed, but by an exception other than an assertion (not a catch)
  SURVIVED  every test passed
  WRONG     tests failed, but not the named one
  CRASHED   no pytest summary line
  TIMEOUT   the run did not end inside the arm's time limit
  VOID      the anchor is not in the file exactly once
The NULL arm changes a comment only and must survive.

  python skills/upscale/proof/magnific_battery.py [ARM NAME ...]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = "skills/upscale/scripts/magnific.py"
TESTS = "tests/test_upscale_magnific.py"

# (name, old, new, expected test)
ARMS = [
    ("NULL comment only", "# seconds between status checks", "# seconds between checks of status", None),
    ("allowlist removed", "    if not (rx and rx.fullmatch(path)):", "    if False:", "test_allowlist"),
    ("fence matches a prefix", "if not (rx and rx.fullmatch(path)):", "if not (rx and rx.match(path)):",
     "test_allowlist"),
    ("same size change not seen",
     '(hashlib.sha256(data).hexdigest() if data is not None else sha256_of(src)) != quote["input"]["sha256"]',
     "False", "test_run_refuses_same_size_change"),
    ("expiry not checked", '    if time.time() - quote["created"] > QUOTE_TTL:', "    if False:",
     "test_run_refuses_expired"),
    ("claim removed", "    claim(code)\n    try:\n", "    try:\n", "test_concurrent_claim"),
    ("state not checked", '    if quote.get("state") != "quoted":', "    if False:",
     "test_state_blocks_rerun_without_claim"),
    ("price not rechecked", '    if price_of(quote) != quote["price_eur"]:', "    if False:",
     "test_run_refuses_price_change"),
    ("tier edge off by one", "tier = next((t for t in IMAGE_TIERS if edge <= t[0]), None)",
     "tier = next((t for t in IMAGE_TIERS if edge < t[0]), None)", "test_image_tiers_by_long_side"),
    ("priced by area, the cheaper reading", "tier = next((t for t in IMAGE_TIERS if edge <= t[0]), None)",
     "tier = next((t for t in IMAGE_TIERS if out_w * out_h <= t[0] * t[0]), None)", "test_image_tiers_by_long_side"),
    ("quote below Magnific's own example", "    edge = max(out_w, out_h)", "    edge = min(out_w, out_h)",
     "test_quote_never_below_magnific_examples"),
    ("dearest reading not explained", '        notes.append("Magnific charges by the result\'s pixel area',
     '        (lambda *a: None)("Magnific charges by the result\'s pixel area', "test_quote_says_the_charge_can_be_lower"),
    ("precision defaults not named", "            if left:\n", "            if False:\n",
     "test_precision_defaults_are_named"),
    ("broken answer passes through",
     '        except http.client.HTTPException as exc:\n'
     '            raise ConnectionError(f"a broken answer from the server: {exc!r:.100}") from None\n',
     "        except ZeroDivisionError:\n            raise\n", "test_broken_answers_are_network_errors"),
    ("API call not fenced for broken answers", "@_network\ndef _fetch(", "def _fetch(",
     "test_broken_answer_to_the_paid_start_is_unknown"),
    ("download not fenced for broken answers", "@_network\ndef _stream(", "def _stream(",
     "test_broken_answers_are_network_errors"),
    ("upload not fenced for broken answers", "@_network\ndef _put_stream(", "def _put_stream(",
     "test_broken_answers_are_network_errors"),
    ("cut download saved as whole", "    if stated.isdigit() and got != int(stated):", "    if False:",
     "test_cut_download_raises"),
    ("broken status answer ends the wait", '        except OSError as exc:\n            status, answer = None, '
     '{"message": str(exc)}\n', '        except ZeroDivisionError as exc:\n            status, answer = None, '
     '{"message": str(exc)}\n', "test_broken_status_answer_is_a_missed_check"),
    ("cut download not retried", "            except OSError as exc:\n                if attempt == 2:",
     "            except ZeroDivisionError as exc:\n                if attempt == 2:", "test_cut_download_is_retried"),
    ("failed job cannot be fetched", '    if quote["state"] not in ("submitted", "failed"):',
     '    if quote["state"] != "submitted":', "test_completed_without_result_can_be_fetched_again"),
    ("unreadable quote crashes", "    except ValueError:\n        quote = None\n",
     "    except ZeroDivisionError:\n        quote = None\n", "test_unreadable_quote_is_refused"),
    ("another code's quote accepted", '    if not isinstance(quote, dict) or quote.get("code") != code:',
     "    if not isinstance(quote, dict):", "test_unreadable_quote_is_refused"),
    ("missing ffprobe unexplained", '    except FileNotFoundError:\n        raise Refused("ffprobe measures',
     '    except ZeroDivisionError:\n        raise Refused("ffprobe measures', "test_missing_ffprobe_is_explained"),
    ("sound never seen", '"audio": bool(sound.get("streams"))', '"audio": False', "test_dropped_sound_is_reported"),
    ("shallow copy cannot import", 'ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"',
     'ENV_FILE = Path(__file__).resolve().parents[3] / ".env"', "test_imports_from_a_shallow_folder"),
    ("unreadable paid result read as refused", "        except Refused as exc:                   # the job is paid",
     "        except ZeroDivisionError as exc:                   # the job is paid",
     "test_unreadable_result_is_reported_not_refused"),
    ("missing Pillow unexplained", '    except ImportError:\n        raise Refused("Pillow reads',
     '    except ZeroDivisionError:\n        raise Refused("Pillow reads', "test_missing_pillow_is_explained"),
    ("no answer shown as HTTP None", '    head = f"HTTP {status} " if status is not None else ""',
     '    head = f"HTTP {status} "', "test_broken_status_answer_is_a_missed_check"),
    ("frames: the smaller count", "return max(counts) if counts else 0", "return min(counts) if counts else 0",
     "test_frame_count_takes_larger"),
    ("key sent with a download", 'req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})',
     'req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "x-magnific-api-key": api_key()})',
     "test_stream_has_no_key"),
    ("API redirects followed", "with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:",
     "with urllib.request.build_opener().open(req, timeout=timeout) as resp:", "test_api_redirect_not_followed"),
    ("result over http", '    _https(url, "the result")\n', "    pass\n", "test_download_requires_https"),
    ("upload over http", '    _https(url, "the upload address")\n', "    pass\n", "test_put_requires_https"),
    ("key goes to storage",
     'hdrs = {k: v for k, v in headers.items() if k.lower() not in ("x-magnific-api-key", "content-length")}',
     "hdrs = dict(headers)", "test_put_stream_sends_file_and_signed_headers"),
    ("no answer read as safe to retry",
     '        quote.update(state="unknown", error=f"no answer: {exc}")\n        save(quote)\n',
     '        release(code)\n        quote.update(state="quoted")\n        save(quote)\n',
     "test_post_network_error_is_unknown"),
    ("5xx read as refused", "    if 400 <= status < 500:", "    if 400 <= status < 600:", "test_5xx_is_unknown"),
    ("upload failure keeps the claim", "        release(code)                            # nothing paid has gone out",
     "        pass", "test_upload_failure_releases_claim"),
    ("result written over a file", "    if not path.exists():\n        return path",
     "    if True:\n        return path", "test_existing_output_not_overwritten"),
    ("extension taken from -o", "final = unique_path(base.parent / (name + real_extension(part, hint)))",
     "final = unique_path(base.parent / (name + (base.suffix or real_extension(part, hint))))",
     "test_extension_follows_real_format"),
    ("Creative cap removed", '    if mode == "creative" and out_w * out_h > CREATIVE_MAX_PIXELS:', "    if False:",
     "test_creative_cap"),
    ("setting for another upscaler dropped",
     "            raise Refused(f\"--{name.replace('_', '-')} does not apply to a {kind} with --mode {mode}\")",
     "            continue", "test_option_for_other_mode_refused"),
    ("video enlarge check removed", "        if short >= VIDEO_SHORT_SIDE[resolution]:", "        if False:",
     "test_video_must_enlarge"),
    ("key printed", "    status, raw = _fetch(method, API + path, headers, body, timeout)",
     '    print("debug", headers)\n    status, raw = _fetch(method, API + path, headers, body, timeout)',
     "test_key_never_printed"),
    ("fetch adopts a task in any state", '        if quote["state"] not in ("unknown", "submitting"):\n',
     "        if False:\n", "test_fetch_task_only_for_unknown"),
    ("fetch cannot adopt a cut off start", 'if quote["state"] not in ("unknown", "submitting"):',
     'if quote["state"] not in ("unknown",):', "test_fetch_adopts_task_after_cut_off_start"),
    ("output folder taken as a name", "    if base.is_dir():\n        base = base / default_output(quote).name\n",
     "", "test_output_into_existing_folder"),
    ("upload network error not explained", "        if isinstance(exc, OSError):\n",
     "        if False:\n", "test_upload_network_error_releases_claim"),
    ("network error escapes as a traceback", "    except OSError as exc:\n        print(f\"Failed: {exc}\", file=sys.stderr)\n",
     "    except ZeroDivisionError as exc:\n        print(f\"Failed: {exc}\", file=sys.stderr)\n",
     "test_network_error_is_a_clean_failure"),
    ("task id printed after the save", '        print(f"Magnific job {task_id} started.", flush=True)\n        quote.update(state="submitted", task_id=task_id)\n        save(quote)\n',
     '        quote.update(state="submitted", task_id=task_id)\n        save(quote)\n        print(f"Magnific job {task_id} started.", flush=True)\n',
     "test_task_id_shown_even_if_state_cannot_be_saved"),
    ("wait ran out marked failed", "        return 3\n",
     '        quote.update(state="failed")\n        save(quote)\n        return 3\n', "test_wait_timeout_then_fetch"),
    ("check reads 404 as a bad key", "        if status in (401, 403):\n            print(",
     "        if status in (401, 403, 404):\n            print(", "test_check_reads_404_as_no_jobs"),
    ("ledger counts refused jobs", 'SENT_STATES = ("submitting", "submitted", "done", "failed", "unknown")',
     'SENT_STATES = ("submitting", "submitted", "done", "failed", "unknown", "rejected")', "test_ledger_totals"),
    ("turbo polled at its own path", 'STATUS_PATHS = {"/v1/ai/video-upscaler/turbo": "/v1/ai/video-upscaler"}',
     "STATUS_PATHS = {}", "test_turbo_polls_standard_path"),
    ("Creative scale sent as a number",
     'params["scale_factor"] = scale if mode == "precision" else f"{scale}x"', 'params["scale_factor"] = scale',
     "test_creative_payload"),
    ("natural flavour not sent", '            params.setdefault("flavor", "natural")', "            pass",
     "test_turbo_polls_standard_path"),
    ("scale 0 read as 2", "        scale = 2 if args.scale is None else args.scale", "        scale = args.scale or 2",
     "test_creative_scale_choices"),
    ("price rounded down", "math.ceil(round(value * 100, 6)) / 100", "int(value * 100) / 100",
     "test_video_price_counts_frames"),
    ("16 bit source not seen", 'bits = head[24] if fmt == "PNG" else 8', "bits = 8", "test_depth_and_alpha_notes"),
    ("quote goes online", "    quote[\"code\"] = code\n    save(quote)\n",
     "    quote[\"code\"] = code\n    api(\"GET\", \"/v1/ai/image-upscaler\")\n    save(quote)\n",
     "test_quote_makes_no_request"),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, old, new, expect, timeout=300):
    src = (REPO / SCRIPT).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="magnific-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        shutil.copy(REPO / TESTS, f"{tmp}/tests/")
        os.makedirs(f"{tmp}/skills/upscale/scripts")
        mutated = src.replace(old, new)
        Path(f"{tmp}/{SCRIPT}").write_text(mutated)
        line = src[:src.index(old)].count("\n") + 1      # where the fault sits; `new` may be empty
        print(f"  [{name}] {SCRIPT}:{line}: {old.splitlines()[0].strip()[:60]!r} -> "
              f"{(new.splitlines() or [''])[0].strip()[:60]!r}", flush=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="1000")
        with tempfile.TemporaryFile() as out:
            try:
                subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf",
                                f"{tmp}/{TESTS}"], stdout=out, stderr=subprocess.STDOUT, timeout=timeout, env=env,
                               cwd=tmp)
            except subprocess.TimeoutExpired:
                return "TIMEOUT", f"no end inside {timeout} s"
            out.seek(max(0, out.seek(0, 2) - 20000))
            text = out.read().decode("utf-8", errors="replace")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    summary = next((ln for ln in reversed(lines) if SUMMARY.search(ln)), None)
    failed = {}
    for ln in lines:
        m = FAILED.match(ln)
        if m:
            failed.setdefault(m.group(1).split("::")[-1], set()).add(m.group(2) or "?")
    if summary is None:
        return "CRASHED", lines[-1][:200] if lines else "no output"
    if not failed:
        return "SURVIVED", summary
    if expect and expect in failed:
        kinds = failed[expect]
        return ("CAUGHT" if "AssertionError" in kinds else "ERRORED"), f"{summary} | {expect}: {sorted(kinds)}"
    return "WRONG", f"{summary} | failed: {', '.join(failed)}"


def main():
    only = sys.argv[1:]
    results = []
    for name, old, new, expect in ARMS:
        if only and name not in only:
            continue
        verdict, detail = run_arm(name, old, new, expect)
        results.append((name, verdict, expect))
        print(f"{name:38} {verdict:9} {detail}", flush=True)
    faults = [r for r in results if r[2]]
    caught = sum(1 for r in faults if r[1] == "CAUGHT")
    null = next((v for n, v, _ in results if n.startswith("NULL")), "not run")
    print(f"== {caught}/{len(faults)} fault arms caught by their named test; null control: {null}")


if __name__ == "__main__":
    main()
