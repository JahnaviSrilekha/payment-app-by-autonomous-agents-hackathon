#!/usr/bin/env python3
"""Pocketful stage-2 acceptance runner.

One command against a running service:

    python3 run.py --base-url http://127.0.0.1:8080

Options:
    --stage1-url URL   also cross-check a live stage-1 export (R138)
    --skip-ui          API-only run
    --only SUBSTR      run tests whose name contains SUBSTR
    --list             list tests and the requirement coverage, run nothing

Exit code 0 iff nothing FAILED. Skipped UI tests (playwright missing) are
reported but do not fail the run.
"""

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
import api_suite  # noqa: F401  (registers tests)
import ui_suite  # noqa: F401  (registers tests)


def is_ui(fn):
    return getattr(fn, "__module__", "").startswith("ui_suite")


def make_ui_factory():
    from playwright.sync_api import sync_playwright

    class UiFactory:
        def __init__(self, pw):
            self._pw = pw
            self._browser = pw.chromium.launch()
            self._ctx = None

        def new_page(self):
            if self._ctx is not None:
                try:
                    self._ctx.close()
                except Exception:
                    pass
            self._ctx = self._browser.new_context(
                viewport={"width": 1280, "height": 800})
            return self._ctx.new_page()

        def close(self):
            if self._ctx is not None:
                self._ctx.close()
            self._browser.close()

    mgr = sync_playwright().start()
    return mgr, UiFactory(mgr)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--stage1-url", default=None)
    ap.add_argument("--skip-ui", action="store_true")
    ap.add_argument("--only", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--md", action="store_true", help="markdown output for --list")
    args = ap.parse_args()

    tests = core.TESTS
    if args.only:
        tests = [t for t in tests if args.only in t[0]]

    if args.list:
        if args.md:
            print("# Acceptance tests\n")
            for name, reqs, fn in tests:
                print(f"- `{name}` — {reqs}")
            print("\n# Requirement coverage\n")
            cov = {}
            for name, reqs, fn in tests:
                for r in reqs.split():
                    cov.setdefault(r, []).append(f"`{name}`")
            for r in sorted(cov, key=lambda x: (int(x[1:]) if x[1:].isdigit() else 999)):
                print(f"- **{r}**: {', '.join(cov[r])}")
        else:
            for name, reqs, fn in tests:
                print(f"{name:44s} {reqs}")
        return 0

    ui_tests_present = any(is_ui(fn) for _, _, fn in tests)
    ui_mgr = ui_factory = None
    ui_ready = False
    if ui_tests_present and not args.skip_ui and ui_suite.HAS_PW:
        try:
            ui_mgr, ui_factory = make_ui_factory()
            ui_ready = True
        except Exception as e:
            print(f"NOTE: playwright browser unavailable ({e}); UI tests will skip")

    ctx = core.Ctx(args.base_url, args.stage1_url)
    if ui_ready:
        ctx._ui = ui_factory

    results = []
    for name, reqs, fn in tests:
        if is_ui(fn) and (args.skip_ui or not ui_ready):
            results.append(("SKIP", name, reqs,
                            "playwright unavailable" if not args.skip_ui
                            else "--skip-ui"))
            continue
        t0 = time.time()
        try:
            fn(ctx)
            results.append(("PASS", name, reqs, f"{time.time() - t0:.1f}s"))
        except core.Skip as e:
            results.append(("SKIP", name, reqs, str(e)))
        except core.Check as e:
            results.append(("FAIL", name, reqs, str(e)))
        except AssertionError as e:
            results.append(("FAIL", name, reqs, f"assertion: {e}"))
        except Exception as e:
            results.append(("FAIL", name, reqs,
                            f"unexpected {type(e).__name__}: {e}\n" +
                            traceback.format_exc(limit=5)))

    if ui_factory is not None:
        try:
            ui_factory.close()
            ui_mgr.stop()
        except Exception:
            pass

    print()
    for status, name, reqs, note in results:
        print(f"{status:5s} {name:44s} [{reqs}] {note}")
    n_pass = sum(1 for r in results if r[0] == "PASS")
    n_fail = sum(1 for r in results if r[0] == "FAIL")
    n_skip = sum(1 for r in results if r[0] == "SKIP")
    print(f"\n{n_pass} passed, {n_fail} failed, {n_skip} skipped "
          f"({len(results)} tests) against {args.base_url}")

    cov = {}
    for name, reqs, fn in core.TESTS:
        for r in reqs.split():
            cov.setdefault(r, []).append(name)
    print(f"\nRequirement coverage: {len(cov)} requirement ids referenced "
          f"(run --list --md for the table)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())