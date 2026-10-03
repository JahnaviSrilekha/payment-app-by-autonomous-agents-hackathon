#!/usr/bin/env python3
"""Pocketful stage-3 acceptance runner.

One command against a running service:

    python3 run.py --base-url http://127.0.0.1:8080

Options:
    --stage2-url URL   also cross-check a live stage-2 service's export (R273)
    --stage1-url URL   also cross-check a live stage-1 service's export (R273)
    --only SUBSTR      run tests whose name contains SUBSTR
    --list             list tests and the requirement coverage, run nothing
    --md               markdown output for --list

Exit code 0 iff nothing FAILED. Skipped tests (e.g. real stage-2 export
without --stage2-url) are reported but do not fail the run.
"""

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
import api_suite  # noqa: F401  (registers tests)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--stage1-url", default=None)
    ap.add_argument("--stage2-url", default=None)
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
            for r in sorted(cov, key=lambda x: (int(x[1:]) if x[1:].isdigit()
                                                else 999)):
                print(f"- **{r}**: {', '.join(cov[r])}")
        else:
            for name, reqs, fn in tests:
                print(f"{name:46s} {reqs}")
        return 0

    ctx = core.Ctx(args.base_url, args.stage1_url, args.stage2_url)
    h = ctx.api.health()
    if h.status != 200:
        print(f"service health check failed: {h}")
        return 2

    results = []
    for name, reqs, fn in tests:
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

    print()
    for status, name, reqs, note in results:
        print(f"{status:5s} {name:46s} [{reqs}] {note}")
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