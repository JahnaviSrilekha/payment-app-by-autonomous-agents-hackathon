#!/usr/bin/env python3
"""Pocketful stage-1 black-box acceptance suite. Stdlib only.

Usage:
    python3 run.py [BASE_URL] [--filter SUBSTR] [--wait SECONDS] [--list]

BASE_URL defaults to $POCKETFUL_BASE or http://localhost:8080.
Exit codes: 0 = all run tests passed (skips allowed), 1 = failures, 2 = could not reach service.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness  # noqa: E402


def main():
    args = sys.argv[1:]
    base = "http://localhost:8080"
    flt = None
    wait = 30
    if args and not args[0].startswith("--"):
        base = args.pop(0)
    while args:
        a = args.pop(0)
        if a == "--filter" and args:
            flt = args.pop(0)
        elif a == "--wait" and args:
            wait = int(args.pop(0))
        elif a == "--list":
            import tests_core  # noqa: F401  (registers tests)
            import tests_money  # noqa: F401  (registers tests)
            for t in harness.TESTS:
                print("%-52s %s" % (t["name"], ",".join(t["reqs"])))
            return 0
    base = base or os.environ.get("POCKETFUL_BASE", "http://localhost:8080")
    h = harness.Http(base)
    import tests_core  # noqa: F401  (registers tests)
    import tests_money  # noqa: F401  (registers tests)
    if not harness.wait_health(h, wait):
        print("ERROR: no healthy GET /health at %s within %ss" % (base, wait))
        return 2
    results, probe_failures = harness.run_suite(h, only=flt)
    print(harness.render_report(results, probe_failures))
    return 1 if any(r["status"] == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())