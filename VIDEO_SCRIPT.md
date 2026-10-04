# Pebble: video script and recording plan (about 4.5 minutes)

The rules ask for a 3 to 5 minute video, and the room recording is required. This plan mixes the slides, the Band
room, the running app and the harness result. Times in the Band console are your local time (CEST); the logs
are UTC, so add 2 hours to the UTC times below.

## Before you record (10 minutes)
1. Start the app and seed it (README, "Run it locally" and "Create test data"). Log in as `ada@example.com` in one
   browser window, and as `bob@example.com` in a private window.
2. Open the deck full screen (`Pebble-deck.pdf` or the .pptx in presenter view; the notes are your narration).
3. Open the Band console on the stage 2 room, and have the stage 4 room in a second tab.
4. In a terminal, `cd specs/stage-4/acceptance` with the service running, ready to run `python3 run.py --base-url http://127.0.0.1:8080`.
5. Screen recorder: QuickTime (File, New Screen Recording), Loom or OBS. Record at 1080p, microphone on, notifications off.
   Record in one take per section; cut the sections together afterwards.

## Shot list

| Time | On screen | Say (short form; full text is in the slide notes) |
|---|---|---|
| 0:00 to 0:25 | Slide 1 | "We are CoolHackers. Pebble is a shared-pot payments service built in four stages by five AI agents from one dispatch. Adding agents doesn't add parallelism. Adding coordination does." |
| 0:25 to 0:50 | Slide 2 | Four stages, five seats, 160 seat commits, no human message after the dispatch. "In run 1 we needed five." Mention 29.8 hours and about $25 up front. |
| 0:50 to 1:20 | Slide 3, then 4 | Five seats and why: Claude where reasoning pays (coordinator, reviewer), a low-cost model where volume is (developer, tester). Ten seats became five after a toy run cost $12.40. |
| 1:20 to 2:05 | Slide 5, then 6 | The run 1 failure: batch 2 rejected, batches 3, 4 and 5 stacked on the unfixed tip, three "still blocked" messages. The mandate already said a rejection takes priority. Then the fix: a precondition, one branch per batch, a dependency graph. |
| 2:05 to 2:30 | Slide 7 | Walk the table: 5 to 0 human messages, 49 of 65 wasted ticks to 1 of 42, stacked batches 4 to 0, reject to merge 3 h 31 to a median of 15 min. |
| 2:30 to 3:20 | **Band console, stage 2 room** | Scroll to the dispatch (stage 1 room, 15:38 local on 2 Oct, "Build all four stages... I will not reply"). Then the stage 2 room at 04:31 local on 3 Oct: the reviewer's "REJECTING for one defect" on the authorizations screen (message `a18899bf`). Then the developer's fix and the merge. Say: "This is the gate working. One defect, quoted requirement, fixed in 15 minutes." |
| 3:20 to 3:50 | **The running app**, then slide 8 | Desktop width: sign in as Ada, pay Bob, place a hold, show available versus total. Phone width (browser device toolbar, 375 px): the same screens. Mention the 35 committed screenshots. |
| 3:50 to 4:10 | **Terminal** | Run `python3 run.py --base-url http://127.0.0.1:8080`. Show "70 passed, 0 failed, 1 skipped". |
| 4:10 to 4:30 | Slides 9 and 10 | Close-out gate and the honest costs: run 2 spent more, not less (+28% messages, about $25 against $10.50), and about 30% of time was session-limit waiting in both runs. |
| 4:30 to 4:50 | Slide 11 | What we'd do next (route the model per task: advanced model for complex work like UI, cheaper for simple work), reusable mandates, the repo URL. "Thank you." |

If you run long, drop slide 4 and the terminal run. If you run short, show the stage 4 room's close-out roll call
(tester "Clear", 20:26 local on 3 Oct, message `95d2bdd9`).

## Do and don't
- Do say the costs plainly. The deck leads with what improved and then says what did not; judges reward that.
- Do show the Band room in the recording; it is the proof that the agents, not a person, did the work.
- Don't scroll the room fast: stop for two seconds on each quoted message.
- Don't read the slides aloud; say the one idea per slide and let the table or screenshot carry the detail.

## Submission checklist (lablab)
- [ ] Repo URL (public): https://github.com/JahnaviSrilekha/payment-app-by-autonomous-agents-hackathon
- [ ] Deck: `Pebble-deck.pdf` (and the .pptx if the form takes it)
- [ ] Video file or link, 3 to 5 minutes, with the room shown
- [ ] `README.md` and `FACTORY.md` at the repository root, not inside `mandates/`
