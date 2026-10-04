# Pebble (pocketful track)

**We ran the same four-stage build twice, with the same five agents, and changed only how they coordinate.**
Run 2 needed no human message after the dispatch, never stacked work on a rejected batch, and left 35 screenshots
as proof the UI works at phone and desktop width. It also cost more and took longer than run 1, and
[`FACTORY.md`](FACTORY.md) says so, with the evidence.

> Adding agents does not add parallelism. Adding coordination does.

Pebble is a small shared-pot payments service, built in four stages (JSON API; web UI with holds; statements and
corrections; refunds and batch corrections) by four AI seats and a clock program, from one human dispatch, for the
WeAreDevelopers × BAND "Dark Factory" hackathon, pocketful track.

## What changed between run 1 and run 2

| Problem seen in run 1 (room logs) | Change to the mandates | Run 2 result |
|---|---|---|
| A rejected batch kept being built on: batches 3, 4 and 5 stacked on an unfixed tip, and the reviewer said "still blocked" three times | Work is a dependency graph; a batch starts only when every batch it **builds on** is merged | 0 stacked-on-rejected reports |
| "A rejection takes priority" was already in the mandate, and was ignored | Priority became a precondition, not advice | Rejections fixed and merged in 7–58 minutes |
| One shared branch went stale for every batch | One branch per batch, created from latest main | Fewer stale-branch blocks in stage 1 (5 → 2), not eliminated |
| Four per-stage dispatches, a human nudge, a human platform recovery | One dispatch; automated `handover.md` and one room per stage | 0 human messages after the dispatch |
| A fixed 15-minute metronome tick | The timekeeper speaks only when the room is quiet | 1 of 42 ticks in a healthy room (run 1: 49 of 65) |
| UI that looked clumsy | Reviewer takes screenshots at 375 px and 1280 px in every state and commits them | 35 screenshots; a UI defect rejected and fixed |
| Work "done" but not delivered | Close-out roll call before every stage report | A stale `RUN.md` caught at stage 4 verification |
| Expensive reasoning everywhere | Claude on the coordinator and reviewer; a low-cost model on developer and tester | See the cost table in `FACTORY.md` |

## Repository map

| Path | What it is |
|---|---|
| [`FACTORY.md`](FACTORY.md) | Seats, models, setup, design choices and costs, measured time and spend, what failed, how bad work is caught. **Start here.** |
| `stage-1/` … `stage-4/` | One folder per completed stage, each carried forward from the previous one, each with a `RUN.md`. |
| `specs/` | Per-stage requirements, design, ADRs, task graphs (with `builds on` batches), reports, reviewer probes and the screenshots. |
| `mandates/` | Standing instructions for the five seats. Role-only: no product, stack or track text. |
| `room.json`, `room-2.json`, `room-3.json`, `room-4.json` | Band session logs, one room per stage, unedited. |

## Run it locally

Pebble is a single Python process using only the standard library. There is nothing to install.
Use Python 3.12 or newer (the Docker image uses 3.12; the server also starts on 3.11).

**Without Docker**

```sh
cd stage-4
PORT=8080 python3 src/server.py
```

**With Docker**

```sh
cd stage-4
docker build -t pebble-stage4 . && docker run --rm -e PORT=8080 -p 8080:8080 pebble-stage4
```

Then open it:

| What | URL |
|---|---|
| Health check (returns `{"status": "ok"}`) | http://localhost:8080/health |
| Sign up / log in | http://localhost:8080/signup, http://localhost:8080/login |
| Balance and pay | http://localhost:8080/ |
| Requests | http://localhost:8080/requests |
| Split a bill | http://localhost:8080/split |
| Holds (authorizations) | http://localhost:8080/authorizations |

The same paths answer with JSON for API clients that send `Accept: application/json`. Try it at phone width
(375 px) and desktop width (1280 px) in your browser's device toolbar. Each earlier stage has its own folder
and `RUN.md` (`stage-1/` to `stage-4/`); stage 4 includes everything from stages 1 to 3.

The test-control endpoints `POST /_test/reset`, `GET /_test/export` and `POST /_test/import` are enabled and
unauthenticated, by design, so the black-box suite can reset state.

## Create test data

The service keeps everything in memory, so it starts empty. Seed it with `POST /_test/reset`, which replaces all
state with the fixture you send (and returns `204`). Amounts are in minor units: with `"minor_units": 2`,
`10000` is 100.00 EUR. Run this once the service is up:

```sh
curl -s -X POST http://localhost:8080/_test/reset -H 'Content-Type: application/json' -d '{
  "currency": "EUR", "minor_units": 2,
  "users": [
    {"id": "u_ada", "email": "ada@example.com", "password": "hunter2hunter2", "display_name": "Ada", "handle": "ada", "balance": 10000},
    {"id": "u_bob", "email": "bob@example.com", "password": "hunter2hunter2", "display_name": "Bob", "handle": "bob", "balance": 5000},
    {"id": "u_cyd", "email": "cyd@example.com", "password": "hunter2hunter2", "display_name": "Cyd", "handle": "cyd", "balance": 2500}
  ],
  "payments": [], "requests": []
}'
```

You now have three users who can log in with the password `hunter2hunter2`. Reset again any time to start clean.
Fixtures can also seed `payments`, `requests`, `authorizations` (holds) and `settlement_operator_ids`;
`specs/stage-4/acceptance/core.py` (`fx_pay`, `fx_auth`, `fixture`) shows every field.
To save a state and bring it back later: `curl -s http://localhost:8080/_test/export > state.json`, then
`curl -s -X POST http://localhost:8080/_test/import -H 'Content-Type: application/json' -d @state.json`.

## Try the app yourself

**In the browser.** Seed the data, then open http://localhost:8080/login and sign in as `ada@example.com` /
`hunter2hunter2` (or create a new account at `/signup`). Then walk the screens, with the browser's device toolbar
at 375 px and again at 1280 px:

1. `/` shows Ada's balance. Pay `bob` an amount and check the balance drops.
2. `/requests` lists incoming and outgoing requests. Pay, decline or cancel one.
3. `/split` splits an amount between `bob` and `cyd`, with a preview before you submit.
4. `/authorizations` places a hold. Held money leaves your *available* balance but not your *total* until it is captured or voided.

Open a second browser profile signed in as Bob to see the other side of each action.

**From the command line** (after seeding). Each call below was run against this repository's stage 4 and returned what is noted:

```sh
B=http://localhost:8080; J='Content-Type: application/json'

# log in as Ada and keep her token
TOK=$(curl -s -X POST $B/auth/login -H "$J" -d '{"email":"ada@example.com","password":"hunter2hunter2"}' \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s $B/me -H "Authorization: Bearer $TOK"        # balance 10000, held 0, available 10000

# pay Bob 7.00; writes need an Idempotency-Key, and repeating the same key replays the original (HTTP 200)
curl -s -X POST $B/payments -H "$J" -H "Authorization: Bearer $TOK" -H "Idempotency-Key: demo-1" \
     -d '{"to_handle":"bob","amount":700,"note":"lunch"}'

# split 10.00 between Bob and Cyd: two pending requests of 500 each
curl -s -X POST $B/splits -H "$J" -H "Authorization: Bearer $TOK" -H "Idempotency-Key: demo-2" \
     -d '{"amount":1000,"participant_handles":["bob","cyd"]}'

# hold 2.00 for Cyd: /me then shows balance 9300, held 200, available 9100
curl -s -X POST $B/authorizations -H "$J" -H "Authorization: Bearer $TOK" -H "Idempotency-Key: demo-3" \
     -d '{"to_handle":"cyd","amount":200}'

# Ada's statement, then a refund of 3.00 by Bob (use the payment_id returned by the payment above)
curl -s "$B/statement?limit=5" -H "Authorization: Bearer $TOK"
curl -s -X POST $B/payments/<payment_id>/refunds -H "$J" -H "Authorization: Bearer <BOB_TOKEN>" \
     -H "Idempotency-Key: demo-4" -d '{"amount":300}'
```

Things worth trying on purpose: pay more than the balance (rejected, balances unchanged), repeat a write with the same
key (replayed, no second transfer), repeat it with the same key and a different body (rejected), and refund more
than the payment (rejected). The full list of rules is in `specs/stage-N/requirements.md`.

## Test it

Start the service as above, then in a second terminal from the repository root. The suites seed their own data (every
test resets the service first), so no manual seeding is needed:

```sh
# 1. Black-box acceptance suite: every requirement R291-R334, plus the carried stage 1-3 suites
cd specs/stage-4/acceptance
python3 run.py --base-url http://127.0.0.1:8080      # exit code 0 means nothing failed

# 2. Unit tests (Python, standard library only)
cd ../../../stage-4
python3 -m unittest discover -s tests -q

# 3. UI script tests (Node, no packages)
for f in tests/app_*_tests.js; do node "$f"; done
```

Expected results (we ran all three against a fresh stage-4 server on Python 3.11): acceptance `70 passed, 0 failed, 1 skipped`
(the skipped test cross-checks a live stage-2 service and needs `--stage2-url`); unit tests `Ran 425 tests ... OK`
(about 4 minutes); all six Node test files print `all N ... tests passed`.

`specs/stage-4/acceptance/COVERAGE.md` maps each requirement id to its test.

## Check the repository

The organisers' offline check of the whole repository, run from the kickoff package:

```sh
cd dark-factory-wearedevs
.venv/bin/python -m harness check ../payment-app-by-autonomous-agents-hackathon --track pocketful
```

## History

Every product commit was made by a seat under its own name (`coordinator`, `developer`, `reviewer`, `tester`).
The only human-authored commit is the docs commit adding `README.md` and `FACTORY.md`.
