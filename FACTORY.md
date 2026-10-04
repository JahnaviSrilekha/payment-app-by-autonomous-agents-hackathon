# FACTORY.md: Pebble (pocketful track)

> **Adding agents does not add parallelism. Adding coordination rules does.**
> We ran the same four-stage build twice with the same five seats. The only thing we changed between
> the runs was the operating model: dependencies, priorities, handoffs, batching, Git rules, evidence
> and recovery. This file shows what broke in run 1, what we changed in the mandates, and what the
> room logs say happened in run 2, including what did not get better.

Evidence rules: every claim cites a room log (`room*.json` here, or the run-1 exports named below) by
time (UTC) and message id prefix, or a mandate by file and quoted text. Run 1 is the judged practice run of
30 Sep – 1 Oct (rooms `Build_Pocketful_stage1_stage2_stage3_stage4_` and `Build_Pocketful_Cont_Stage4`;
mandates at tag `judged-run-1` of the team repo). Run 2 is this repository (`room.json`, `room-2.json`,
`room-3.json`, `room-4.json`; mandates in `mandates/`).

## 1. Result at a glance

| | Run 1 | Run 2 |
|---|---|---|
| Human messages after the first dispatch | **5**: three per-stage dispatches, one nudge, one platform-recovery message | **0**: one dispatch built all four stages |
| Rooms | One room, full at 10,000 messages in stage 4 (every later send rejected), then a continuation room | One room per stage, largest 4,621 messages |
| Stage handover between rooms | Written by the human | Automated: coordinator writes `handover.md`, timekeeper wakes it in the new room |
| Reviewer reports a batch "stacked on an unfixed rejected batch" | 4 messages, one chain, stage 2 | **0** |
| Reject → fixed and merged | Stage 2 batch 2: fix first verified 3 h 31 min later, still not mergeable | 7, 14, 15, 15 and 58 minutes (five rejections) |
| Timekeeper ticks sent while the room was healthy (last activity under 10 minutes ago) | **49 of 65** (75%) | **1 of 42** (2%) |
| UI evidence | None required | 35 committed screenshots at 375 px and 1280 px, in empty, loading, error and populated states |
| Close-out before a stage report | None | Roll call to every seat, no unmerged branch, every handoff answered |
| Claude session-limit windows | 5 | 5 (not improved: see section 7) |
| Wall-clock | 25.6 h | 29.8 h (not improved) |
| Messages | 11,053 | 14,182 (+28%, not improved) |
| Featherless spend | about $10.50 | about $25 (not improved) |

The honest reading: run 2 is more reliable, fully unattended and better evidenced. It is not cheaper or
faster in absolute terms, and we do not claim it is. Section 6 explains the cost and why we accept it.

## 2. The factory

Five Band seats. Four are models, one is a clock program. One human dispatch, no steering.

| Seat | Harness · model | Owns | Can block |
|---|---|---|---|
| coordinator | Claude Code · claude-sonnet-5 | Requirements (R-ids), design, ADRs, the dependency graph, task list, routing, close-out, stage reports | A handoff missing spec, revision or evidence |
| reviewer | Claude Code · claude-sonnet-5 | Clean-copy review, visual review, the only fast-forward merges, stage verification | Any failing check, missing R-id evidence, broken invariant, test-fitting |
| developer | OpenCode · featherless/zai-org/GLM-5.3-Flash | Tasks with unit tests, one branch per review batch | n/a |
| tester | OpenCode · featherless/zai-org/GLM-5.3-Flash | Completeness review, black-box acceptance suite, invariant attacks, UI checks | A requirement with no test |
| timekeeper | Band CLI script · none | Wakes the coordinator when a stage goes quiet; follows it to the next room | Never decides |

### Seats follow specialization, not job titles
Our original design had ten seats mirroring a human org chart (architect, two developers, QA explorer,
release manager, SRE, log watcher). A three-seat toy rehearsal (28 Sep, GLM-5.2) showed that cost and
failures came from coordination, not capacity: 410 billed requests and $12.40 for about a stage and a half,
a coordinator that gave up after two minutes and wrote the code itself, replies that were never delivered,
chatter ("Thank you", "Acknowledged") eating turns. So we cut to four model seats (`PLAN.md` §10):

- **Architect is a responsibility, not a seat.** The coordinator already wrote good requirements in the
  rehearsal, and a separate architect adds a full-spec handoff per stage.
- **One developer, not two.** A second developer doubles handoffs, merges and resend risk. Distribution of
  work is visible instead in four seats with distinct committed work.
- **QA explorer folds into the tester,** which already drives the service.
- **The ship-and-watch loop was cut:** it is not scored, and each extra seat is more wake-ups.

The rule we now apply: *add a seat only when its specialization is worth more than the messages and tokens
it adds to every handoff.*

### Models are routed by workload
| Workload | Seats | Model | Why |
|---|---|---|---|
| Reasoning-heavy: planning, decomposition, spec interpretation, corner cases, merge decisions | coordinator, reviewer | Claude (claude-sonnet-5) | Quality of the plan and of the review decides how much rework follows. These two seats ran on a flat subscription. |
| Volume-heavy: code and tests | developer, tester | GLM-5.3-Flash on Featherless | The developer writes most of the code. GLM-5.3-Flash is about 9× cheaper than the GLM-5.2 we used in the toy run ($0.15/$0.50 vs $1.40/$4.40 per million input/output tokens). The reviewer's independent probes compensate for the weaker author. |

In the current design the choice is made once, per seat, by reasoning load: the seats that need the most reasoning
(planning, decomposition, corner cases, merge decisions) run on a high-end model, Claude Sonnet, and the seats that need
less reasoning per token (writing code and tests at volume) run on a cheaper model. It is a static split. It does not yet
look at how hard an individual task is (section 7 describes the next step).

The mandates carry `Harness:` and `Model:` lines for this reason (`mandates/developer.md`:
`Model: featherless/zai-org/GLM-5.3-Flash`; `mandates/reviewer.md`: `Model: claude-sonnet-5`).

### Setup
- Python 3.12 harness venv, Playwright Chromium, Docker, on the host.
- `setup-seats.sh` creates the seats in Band, each live-linked to its mandate. Mandates are generated from
  `tools/build_mandates.py` (one shared rule set plus one section per seat) and checked by
  `tools/scan_mandates.sh`. They contain no product, stack or track text: all of that is in the dispatch,
  so the same mandates can be pointed at a different problem.
- Pre-allowed command lists (`claude-settings.json`, `opencode-seats.json`) deny `git push`, `sudo`, the shipped
  test files and the harness source. OpenCode seats also cap tool output (300 lines / 16 KB), prune old
  outputs and compact early to keep context, and cost, small.
- `tools/start-stage.sh all` runs preflight checks, creates the timekeeper, and puts the single dispatch
  on the clipboard. The human then sends one message and stays silent.

## 3. What run 1 taught us

Observations that drove the redesign, each with its evidence.

**1. A rejection did not stop the pipeline.** In stage 2 the reviewer rejected batch 2 with two defects
(`b142c1e0`, 19:50). The developer kept building on that tip, so batches 3, 4 and 5 were all stacked on
unfixed code. The reviewer then had to say so three times, each time a little more tired:

> "Batch 3 (T27, 6184525) — blocked, not reviewed yet: it's stacked directly on batch 2's tip … I can't
> fast-forward to 6184525 without also pulling in the unfixed batch-2 defects." (`1e00da5c`, 20:04)
>
> "Batch 4 (T28-T33, 4643ebe) — still blocked, same reason as batch 3 … I haven't seen a batch-2 fix land yet."
> (`567b4a81`, 22:32)
>
> "Batch 5 (T34-T36, 6720463) — same blocker as batches 3 and 4." (`499180de`, 22:57)

Two minutes later the coordinator asked whether the pipeline was stalled, and the reviewer had to answer
"Pipeline isn't stalled on my end — I replied to all five batches" (`9bfa4d3f`, 22:59). The fix was first verified
good at 23:21, but even then could not merge because its base was stale. Hours of developer work (T27–T36)
sat on a defective base.

Notably the run-1 developer mandate already said *"A rejection takes priority over everything."*
(`judged-run-1:mandates/developer.md`, line 34.) The text was not enough: the same mandate told the developer to
"start the next batch while the review runs" on a single branch "named after your seat". **A priority rule the
agent cannot check mechanically does not hold. We replaced it with a precondition** (section 4).

**2. Stale branches kept blocking merges.** In stage 1 the reviewer reported the same non-fast-forward blocker for
batches 2, 3 and 4 in a row: *"Still not a fast-forward though — 3rd batch in a row, dev-stage1 still lacks main's tip
as an ancestor despite two prior requests"* (`35f0be55`, 16:18). All batches shared one branch, `dev-stage1`.

**3. Handoffs were lost, and the room filled up.** After stage 1's work was done, one handoff went to the wrong
seat and nothing woke anyone for an hour; the human nudged: *"the tester's suite tester-stage1@066bb54 awaits your
merge, then run stage verification"* (`a3f2a8f7`, 18:20). In stage 4 the room hit Band's 10,000-message cap. The human's
recovery message: *"The previous room … reached Band's 10,000-message-per-room limit at 14:01 CEST … every
message sent after that was rejected, so some handoffs were never delivered"* (`62dc83e2`, 14:17 on 1 Oct).

**4. The timekeeper was a metronome.** `judged-run-1:tools/timekeeper.sh` sent a tick every 15 minutes
whether or not anything was wrong. 49 of its 65 ticks arrived while the room had seen activity in the last ten
minutes, and each one cost the coordinator a turn.

**5. Cost came from coordination.** The toy rehearsal's $12.40 for about a stage and a half (about half of our
Featherless balance) was the alarm: more seats means more handoffs, more messages, more re-sent context.

**6. UI looked functional but clumsy.** Endpoint tests passed while the screens looked rough. Nothing in run 1
required anyone to look at the page.

## 4. What we changed, and the text that changed it

All quotes are from `mandates/*.md` of this repository unless marked.

### 4.1 Work is a dependency graph before it is a task list
> "Give every batch a "builds on" line: `none`, or the batches whose code it needs (it calls their code, extends
> their files, or its tests need their behaviour). Batches that touch the same files build on each other. Arrange
> the batches so that at least two can start from the stage's first revision wherever the design allows, so the
> developer has independent work while a review runs. A batch counts as available to build on only once
> `@reviewer` has accepted it and merged it into main." (`coordinator.md`, planning step 4)

The coordinator's task lists show it using the rule, including saying when parallelism is *not* available:

- Stage 2 `tasks.md`: *"Batch 1 and batch 2 share no files and no runtime dependency on each other — both can be
  developed and reviewed in parallel."* (data model and visual system start together)
- Stage 3: *"Batch 1 and batch 2 touch disjoint files … both can be developed and reviewed in parallel."*
- Stage 1 and stage 4 are single chains, and the coordinator records why: *"a hard dependency chain … recorded
  as a design constraint, not an oversight"*; *"Every new surface in this stage shares logic with every other one"*.

Tasks have an explicit `dependencies` column (`T21 … T10, T11, T12, T13, T14, T16`), so "wait" and "go" are
decided by the graph, not by an idle agent.

### 4.2 A rejection gate: a precondition, not advice
> "Take batches in the order of `tasks.md`, but start a batch only when every batch in its "builds on" line is
> merged into main. While a review runs, work on the next batch that is ready by that rule. If none is ready,
> wait: the reviewer's acceptance message wakes you, and building on unmerged code means redoing and retesting it
> if that review fails." (`developer.md`)
>
> "Only code the reviewer has accepted and merged into main can be built on. A batch that is committed but not
> yet reviewed, in review, or rejected is never a base for other work, and you never branch from your own
> unmerged batch branches." (`developer.md`)

The run-1 failure cannot be reproduced under this text: a batch that builds on a rejected batch is not ready, so
it is never started. In the run-2 logs the reviewer never reports a batch stacked on a rejected one.

### 4.3 Reviews are batches, because agents are not humans
Humans review small diffs because humans cannot hold the whole system in their heads. Agents can. So the
developer commits each task for traceability, but the unit of review is a coherent batch of three to five
per stage:

> "… tasks into three to five review batches, each a coherent slice that can be reviewed on its own, in
> dependency order." (`coordinator.md`)
>
In the 29 Sep practice run every review re-ran the full harness (`PLAN.md` §11). The reviewer mandate now says:

> "Run the unit tests and the acceptance tests for those requirement ids yourself, then probe the claimed
> requirements with your own black-box checks … The full supplied check command runs once, at stage verification,
> not in batch reviews." (`reviewer.md`)

### 4.4 Communication is an interface
- One complete handoff per message; a follow-up names the handoff it continues and carries only what is new
  (`developer.md`: *"A follow-up about work already handed off in this stage … names the handoff it continues and
  carries only what is new … Any new assignment is a full handoff."*).
- Delivery is not "I sent it". The coordinator checks it each time it wakes:
  > "…whose move is it now, and was that seat actually sent the request? Is `@developer` idle while a batch whose
  > "builds on" batches are all merged is still open? If so, assign it." (`coordinator.md`, step 7 "Keep the stage moving")
- Silence handling is fixed: wait, resend once, identical and complete, then escalate as a blocker (shared "Waiting" rule).
- No acknowledgement chatter beyond one receipt (shared rules).

### 4.5 Git is part of the protocol
> "Work in your own git worktree of the result repository, on one branch per review batch, named after your
> seat, the stage and the batch (for example `developer-s2-b3`). Create it from the latest main revision, which
> must already contain every batch it builds on." (`developer.md`)
>
> "A batch branch made from an older main revision cannot be fast-forwarded once another batch has merged: merge
> the main branch into it (as your seat), rerun its unit tests and the acceptance tests for its requirement ids,
> and re-request review with the new commit hash." (`developer.md`)

Every handoff carries the exact branch and commit hash, the reviewer reviews that hash (not "the branch tip"), and
only the reviewer fast-forwards `main`. The tester and the coordinator get the same discipline
(`tester-s<N>`, `coordinator-s<N>-docs`, each created from latest main). The log shows seat-attributed commits all
the way through: coordinator 25, developer 78, tester 38, reviewer 19.

### 4.6 UI quality is evidence, not opinion
> "For a batch that changes a user interface, also run a visual review: start the service from your checkout,
> take browser screenshots of every changed screen at a narrow phone width (375 px) and a desktop width
> (1280 px), in each state the source text names (empty, loading, error, success and the rest), commit them
> under `specs/<stage folder name>/screens/`, and look at them against the source text's product and visual
> requirements and the design's visual system." (`reviewer.md`)
>
> "A visual rejection names the stated quality requirement it misses and the screenshot path that shows it."
> (`reviewer.md`)

The coordinator writes a visual system (colour roles, type scale, spacing, radius, focus style as named
tokens; shared components; layout at both widths) *before* any screen is built, and the first UI task builds the
shared styles that every screen task builds on (`coordinator.md`). The tester turns the qualities into tests:
*"no horizontal scrolling at the phone width, a visible label for every input, visible keyboard focus, and every
named state reachable and distinct"* (`tester.md`).

Result: `specs/stage-2/screens/` holds 35 screenshots (for example `home_pay_loading_375.png`,
`authorizations_error_375.png`). One batch was **rejected** for a UI defect the visual review exposed
(*"authorize-error/authorization-error render in the wrong part of the page"*, `a18899bf`, 02:31 on 3 Oct), and the
fix is in the repository as `authorizations_error_fixed_375.png`. The reviewer wrote *"all look correct in my own
screenshots"* about the rest of that batch.

### 4.7 A close-out gate before any report
> "Close out. Before the report, prove nothing is left behind: every task on the room's task board is completed or
> removed; `git branch --no-merged main` shows no seat branch with unmerged work …; every handoff in the room has
> its answer; one roll call to every other model seat: "Stage N close-out: reply `clear`, or list what is still
> open." An open item goes back to its owner ahead of all other work; repeat the roll call once it is done."
> (`coordinator.md`, step 10)

At the end of stage 4 the tester answered *"Clear — tester side fully closed at main b4a7469"* (`95d2bdd9`, 18:26), and
the reviewer's stage verification caught a carry-forward leftover, `stage-4/RUN.md` still labelled stage 3, which the
developer fixed before the report (`b4a7469`).

### 4.8 Recovery comes from durable state, not conversation
- State lives in Git and in files. The next stage's room starts with no memory; the coordinator writes a standalone
  `handover.md` (the human's task verbatim, the stage, the verified revision, paths, open decisions) and then the
  new room id to `current-room`:
  > "…then write the new room's id, alone on one line, to the file `current-room` in the same folder. `@timekeeper`
  > sees that file and immediately sends you a message in the new room that points you to `handover.md`; that
  > message is your cue to start there." (`coordinator.md`, step 12)
- A seat that receives an incomplete handoff asks for it. *"Never reconstruct it from room history or from the code."*
- Run 1's platform recovery needed a human to write the recovery message. In run 2 the timekeeper sent the three stage
  handover wakes itself (`e20c1683`, 17:21 on 2 Oct, and two more), with no human message.

### 4.9 The timekeeper is an exception detector
`tools/timekeeper.sh` (run 2) ticks only if the newest message in the room is older than `QUIET` (600 s):
*"the room is active -> no tick (nothing is stuck, no turn is wasted)"*. It backs off when the last message was its
own tick, and it follows the coordinator to the next room. Result: 1 of 42 ticks fired in a healthy room, against
49 of 65 in run 1.

## 5. How the factory catches bad work

| Failure | Caught by | Real example from the logs |
|---|---|---|
| Missing or contradictory requirement | Tester's completeness review before any code | Stage 1: design §5 contradicted R63 on idempotency ordering (`2d135161`, 13:53). Stage 4: R303's wording contradicted its own acceptance criterion about who is debited on a refund (`f52a7dc2`, 13:04 on 3 Oct) |
| Wrong behaviour | Reviewer's own black-box probes and the tester's independent suite | Stage 1 batch 3, R79: validation precedence was wrong with 112/112 unit tests green |
| Visual or recovery defect | Reviewer's screenshots, tester's UI checks | Stage 2 batch 5: errors rendered in the wrong place |
| Data integrity | Reviewer's invariant stress probes | Stage 3 T23, R218: reset never checked the opening balance was non-negative |
| Crash on state the unit tests never built | Reviewer exercises export with real state | Stage 3 batch 4: `GET /_test/export` crashed once a statement snapshot existed |
| Contract gap | Reviewer reads the interface against the requirement | Stage 4 T33, R325: `correction_batch_id` missing from `GET /revisions` |
| Integration drift | Clean clone and isolated harness at stage verification | Stage 4: stale `RUN.md` label caught in verification |
| Unreviewed or unfinished work | Close-out roll call and `git branch --no-merged main` | Section 4.7 |
| Test-fitting | Reviewer rejects code that branches on test inputs; seats cannot read the shipped tests | Permission files deny the test folders |

Rejection-to-merge times in run 2: 58, 15, 15, 7 and 14 minutes. The first fix cycle in run 1's stage 2 took more than
three and a half hours to verify, because three more batches were piled on top of it.

## 6. What it cost, honestly

| | Run 1 | Run 2 |
|---|---|---|
| Wall-clock, first dispatch to last report | 25.6 h | 29.8 h |
| of which waiting on the Claude session limit | about 8 h | about 9 h |
| Messages (tool calls, results, thoughts, tasks and text) | 11,053 | 14,182 |
| Tool calls | 3,927 | 4,919 |
| Seat commits | 132 | 160 |
| Featherless spend | about $10.50 (dashboard $13.82 for the day, including the last practice run) | about $25 |
| Claude seat tokens | about 359 M (subscription) | not yet read from usage |

Per stage (run 1 → run 2): hours 4.4→3.7, 7.5→9.7, 8.6→9.6, 5.1→6.8; messages 2,226→2,607, 3,513→4,621,
3,000→3,768, 2,314→3,186.

What this says:
- **Run 2 spent more, not less.** We do not claim a cost saving between the runs. The cost win came earlier: from the toy
  rehearsal on GLM-5.2 (about $8 per stage) to GLM-5.3-Flash with Claude on the two reasoning seats (about $2.60 per stage
  in run 1).
- **Run 2 bought verification:** 35 screenshots, a visual review on every UI batch, a close-out roll call per stage,
  five reviewer rejections caught and fixed, and four rooms each opened with a full handover. We did not isolate how much of
  the extra Featherless spend each one cost; the developer and tester seats sent 21% more messages while spend rose
  about 2.4×, so some of it is context that every fresh room re-reads.
- **Coordination overhead is a design target, not a side effect.** Batching, "no ack chatter", complete-or-nothing
  handoffs and a quiet timekeeper all aim at fewer wasted turns per unit of verified work. We measured the timekeeper and
  the stacked-rejection pattern. We did not measure the others against a control, so we do not claim a number.

## 7. What failed, and what is still not solved

- **Claude session limits.** Both runs hit five session-limit windows, about 30% of wall-clock each time. The seats resumed
  on their own within about 25 minutes of every reset (run 1: 4–25 min; run 2: 2–15 min). Run 2's windows:

  | Stage | First error (UTC) | Reset (CEST) | Resumed | Lost |
  |---|---|---|---|---|
  | 1 | 15:37 reviewer | 18:10 | 16:26 | 0.8 h |
  | 2 | 18:29 reviewer | 23:10 | about 21:21 | 2.9 h |
  | 3 | 05:28 reviewer | 09:20 | about 07:34 | 2.1 h |
  | 3 | 11:20 coordinator | 14:30 | 12:35 | 1.3 h |
  | 4 | 15:22 reviewer | 19:30 | about 17:42 | 2.3 h |

  A weekly-limit error also hit the reviewer at 18:29 in stage 4 (`f4d544f9`) and delayed the last merge by about 30
  minutes. **Our process did not reduce limit exposure.** Only a failover model for the Claude seats would.
- **The timekeeper cannot wake a seat whose account is exhausted.** 27 of run 2's 42 ticks landed on a coordinator that was at
  its limit and failed with the same error. The backoff keys off "newest message is our own tick", but each failed turn
  posts its own error messages, so the backoff never engages. The next fix: have the timekeeper read the limit's reset
  time from the error and stay silent until then. Also, `mandates/timekeeper.md` still describes a fixed 15-minute tick;
  the exception behaviour lives in `tools/timekeeper.sh`.
- **Non-fast-forward merges were reduced, not eliminated.** Run 1 had 12 such reviewer reports (5 in stage 1);
  run 2 had 10 (2 in stage 1). Branches built from latest main still go stale when a parallel batch merges first, and
  the reviewer wrote *"same fix as every batch, one more rebase"* (`c17eb5ac`, stage 2). The next fix is to have the
  developer merge main immediately before handing off, or the reviewer produce the merge commit.
- **Model choice is fixed per seat, not per task.** A complex task such as UI work and a one-line fix get the same
  developer model. The next step is to route at run time: simple tasks stay on the cheaper model, and complex ones
  (UI, concurrency, money invariants) go to a more advanced model, for the developer and the tester. We expect cost to
  rise, but wall-clock time to fall (fewer reject-and-fix cycles) and quality to improve. We have not measured this; it is
  a hypothesis for the next run, not a result.
- **Throughput.** Stage 2 and stage 3 took 9–10 hours each, including limit waits.
- **Run 1 only:** the 10,000-message room cap and the lost handoff that needed a human. Neither recurred in run 2.

## 8. Why this is an agentic process, not a human one

A human team needs frequent small synchronizations because people cannot keep the whole system in mind. Agents can read
the spec, the code, the tests and the Git history at once. So we designed the work as:

| Human habit | Agentic replacement |
|---|---|
| Roles mirror an org chart | Seats mirror specializations; architecture is a responsibility of the coordinator |
| One small pull request at a time | One coherent batch per review, tested as an integrated capability |
| "Please prioritise this" | A precondition: dependent work does not start until its prerequisite is merged |
| Daily standup | An exception detector that speaks only when the room is quiet |
| "I told them" | Delivery is a durable commit plus a handoff that names the next owner and action |
| Memory of the last meeting | `handover.md`, `tasks.md` and Git as the source of truth |
| "Looks fine" | Screenshots, a probe, a command and its output |

No hurry, no uncontrolled parallelism. Understand the work graph, run what can run independently, wait where
dependencies exist, batch what should be reviewed together, spend expensive reasoning only where it pays, and recover
from durable state.

## 9. Reuse

To point the factory at a different problem, write a new dispatch. `mandates/` names no product, stack or track, and the
rules in it (dependency graph, rejection gate, batch review, handoff contract, close-out, handover) hold for any
multi-stage build.
