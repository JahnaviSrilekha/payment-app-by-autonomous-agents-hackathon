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

## Run a stage

```sh
cd stage-4
cat RUN.md
```

## Check the repository

```sh
cd dark-factory-wearedevs
.venv/bin/python -m harness check ../payment-app-by-autonomous-agents-hackathon --track pocketful
```

## History

Every product commit was made by a seat under its own name (`coordinator`, `developer`, `reviewer`, `tester`).
The only human-authored commit is the docs commit adding `README.md` and `FACTORY.md`.
