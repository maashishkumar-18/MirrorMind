# Golden Eval Set — Independent Second-Reviewer Review

Reviewed by: independent fresh-context agent (not the set author). Phase 1 Step 1.4b.

Scope: `eval/seed_corpus.json`, `eval/golden_qa_set.json` (86 items), `eval/run_eval.py`
scoring functions, `eval/README.md`, `tests/eval/test_harness_smoke.py`, and
`audits/production_roadmap.md` Step 1.4 (lines 315-389) + the Phase 4 gate table
(lines 775-783). This review tries to falsify the set's quality claims.

Roadmap gate implemented: Step 1.4 "Eval set second-reviewer gate" (line 361).
Product-spec Risk #6 mitigation ("old eval set gives false confidence in release readiness").

---

## Sample

35 items reviewed for ground-truth accuracy (>= 20 required), spread across every
category and every disambiguation tier:

- **conversation_recall:** q001, q002, q003, q009, q012, q015, q017, q018
- **reminder:** q019, q022, q024
- **todo:** q026, q030
- **meeting:** q031, q033, q036
- **schedule:** q037, q038, q042, q043
- **paraphrase_recall:** q044, q045, q046, q048, q049, q050
- **summary_request:** q055
- **conversation:** q058, q060, q061
- **action_request:** q062, q065, q066
- **unanswerable:** q067, q074, q082, q083

Tier coverage in the sample: Tier 1 (q001-q003, q009, q015, q017, q019, q022, q026,
q030, q031, q033, q037, q038, q042, q055, q062, q065, q066, q067, q074, q083),
Tier 2 (q012, q018, q024, q036, q043, q044, q045, q046, q048, q050), Tier 3 (q049, q061),
Tier 4 (q058, q060, q082).

---

## (a) Ground-truth accuracy

**Verdict: PASS WITH NOTES.**

Every answerable ground truth in the sample is directly supported by a specific seed
message or structured record. Every unanswerable item in the sample is a genuine
negative — the specific fact is absent from the full corpus. Two items have weak /
under-anchored ground truth (q024, and to a lesser extent q036); neither feeds a
gated metric, so the impact is contained. No item is mislabelled answerable ↔
unanswerable. Cross-checked calendar consistency: Feb 15 2026 = Sunday, Feb 16 =
Monday, Feb 19 = Thursday, Feb 20 = Friday — all consistent with `schedules`
titles and the session text ("Monday morning", "Thursday evening", "weekend before").

| id | verdict | supporting seed location | problem |
|---|---|---|---|
| q001 | PASS | session s1 msg 2 (`s1-m2`): "let's move the launch to Friday" | — |
| q002 | PASS | s1 msg 4: "Priya is going to own the launch checklist"; also m1.decisions | — |
| q003 | PASS | s1 msg 6: "launch budget stays capped at 5000 dollars"; m1.decisions[2] | — |
| q009 | PASS | s3 msg 2: "Dana is coming over the weekend before to help me pack the kitchen" | — |
| q012 | PASS | s4 msg 2: "I need to be about halfway through by then" | — |
| q015 | PASS | s7 msg 2: "the shop said about 400 dollars for the front pads and rotors" | — |
| q017 | PASS | s10 msg 0: "Sam's birthday is March 3rd" | — |
| q018 | PASS | m1.searchable_text: "Sam preps the announcement blog post"; m1.action_items[1] | fact lives only in the meeting note, not in s1 — `expected_route: hybrid` is defensible (see (b)) |
| q019 | PASS | reminder r1 title: "Pack the tent for Portugal" | — |
| q022 | PASS | reminder r3 title: "Pay the electricity bill" (only "bill" reminder) | — |
| q024 | PASS WITH NOTES | reminders r6 (2026-02-15), r3 (2026-02-20) | "this week" / "coming up" has **no anchor date** — the harness passes only the question string to the pipeline, no "today". The chosen reminders are not even the nearest-term ones (r5 2026-02-11, r2 2026-02-12 are earlier). GT is defensible only because `notes` says "any subset ... scored loosely"; `is_temporal:true` but GT carries no temporal token so `score_temporal` returns `None` (not scored). Low risk, but the item is soft. |
| q026 | PASS | todo t2: priority "high", category "work" (only work todo) | — |
| q030 | PASS | todo t3: priority "high" | — |
| q031 | PASS | m1.decisions = ["Launch moves to Friday", "Priya owns the launch checklist", "Budget capped at $5000"] | — |
| q033 | PASS | m1.action_items[0]: {task: "Send the updated launch checklist", owner: "Priya", deadline: "Wednesday"} | — |
| q036 | PASS WITH NOTES | m1.follow_ups = ["Confirm marketing is ready by Thursday"] | Question asks "what marketing has to do"; the follow-up is an action for the user/team to *confirm marketing's readiness*, not a marketing task. Semantically loose but the fact is present; correctness is not gated. |
| q037 | PASS | schedule_item si1: start 2026-02-15T08:00:00Z, "Movers arrive"; also s3 msg 0, m3 | — |
| q038 | PASS | si4: start 2026-02-20T14:00:00Z (= 2pm), "Dental cleaning with Dr. Okafor" | timezone risk noted in (b)/floor section — 14:00**Z** must render as "2pm" |
| q042 | PASS | si2: start 2026-02-16T08:00:00Z, "Drop the car at the shop"; s7 msg 2 "Monday morning" | — |
| q043 | PASS | si5 (09:00Z standup) + si4 (14:00Z dental), both schedule_id sc4 (2026-02-20) | — |
| q044 | PASS | todo t6 title: "Keep the whole Portugal trip under 1200 dollars", notes "flights included"; s2 msg 6 | keyword-overlap caveat — see (a) note below + Coverage |
| q045 | PASS | todo t6; s2 msg 6: "I don't want to go over 1200 for the whole thing, flights included" | shares "Portugal" with t6 title |
| q046 | PASS | todo t5 title: "Get fresh basil and pine nuts", notes "for pesto pasta this weekend" | — |
| q048 | PASS | reminder r2 + schedule_item si4 | GT contains "20th ... 2pm" but `is_temporal:false` (cf. q038, same fact, temporal) — a defensible call since the question does not ask about time |
| q049 | PASS | reminders r3/r4, todo t3, session s7 msg 2 | multi-source; notes say any reasonable subset is correct |
| q050 | PASS | reminder r6: "Water the plants", notes "every 3 days while it's dry" | shares "plants" with stored title |
| q055 | PASS | s2 (all turns), todos t1/t6, reminder r1 | every clause traceable |
| q058 | PASS | n/a — pure greeting, GT null | — |
| q060 | PASS | n/a — small talk, GT null | — |
| q061 | PASS WITH NOTES | n/a — GT null, category conversation | borderline: "help me plan something later" could be read as an `action_request`; author flags this in `notes`. Routing expectation is arguable — see (b). |
| q062 | PASS | n/a — action request, GT null; corpus has r1 already but routing-only item | — |
| q065 | PASS | n/a — meeting-note capture, GT null | content ("new nav", "Marcus migration guide") is deliberately NOT in the corpus, correct for a capture item |
| q066 | PASS | n/a — summary action request, GT null, `expected_retrieve_needed:true` | — |
| q067 | PASS | negative confirmed — s3/m3 discuss the move in detail; "wifi" appears nowhere in corpus | — |
| q074 | PASS | negative confirmed — s5/m2: manager is only ever "she"/"my manager"; m2.attendees=["manager"] | — |
| q082 | PASS WITH NOTES | out-of-domain, GT null | "Canberra" is parametric world knowledge the base model likely knows; with `expected_retrieve_needed:false` + `expected_action_type:conversation` the model may just answer it and score as a non-refusal. Intentional test of over-answering, but see the refusal-gate fragility note in Coverage. |
| q083 | PASS | negative confirmed — reminder r3 exists ("Pay the electricity bill"); no amount anywhere | — |

**Ground-truth issues found: 0 hard errors, 4 soft/under-anchored items (q024, q036, q061, q082).**

Keyword-overlap caveat (bears on Coverage, not accuracy): several `paraphrase_recall`
questions share a salient content noun with the source record — q044/q045 ("trip" /
"Portugal" vs t6), q050 ("plants"), q051 ("move/moving"), q052 ("car" / "front"),
q053 ("weekend"), q054 ("kitchen" / "moving"). The *operative* term being paraphrased
(e.g. "spending limit" vs "under 1200", "on-fire" vs "high priority", "before the
plants die" vs "water") genuinely has zero lexical overlap, so the items are valid
paraphrase tests — but under a strict reading of the roadmap phrase ("phrasing does
not share keywords with the stored field value") only ~4-6 of the 11 are fully
lexically disjoint from their source record. Detail under Coverage.

---

## (b) Routing expectations

**Verdict: PASS WITH NOTES.**

`expected_retrieve_needed` / `expected_route` / `expected_action_type` are defensible
for the whole sample. `run_eval.py::score_agentic_routing` only scores `retrieval_route`
when `expected_retrieve_needed` is true, and scores `retrieve_needed` + `action_type`
always — so the null-route items carry real signal on the first two fields.

Flagged (arguable, not clearly wrong):

- **q061** `expected_action_type: conversation`, `expected_retrieve_needed: false`.
  "can you help me plan something later today?" is a plausible `action_request`
  precursor. The author acknowledges the borderline call in `notes`. If the real
  agent classifies this as an action request, it will be scored wrong on 2 of 2
  applicable fields. Recommend either accepting `conversation | action_request` or
  rewording to something unambiguously conversational.
- **q044 / q045** `expected_route: structured`. The spending limit is stored both as
  todo t6 (structured) **and** stated in s2 conversation (semantic). `hybrid` would
  be the safer expectation; `structured` risks penalising a correct hybrid route.
- **q024** `expected_route: structured` is fine, but see the "no anchor date" problem
  in (a) — the routing is the only scorable dimension and it is reasonable.
- **q082** `expected_action_type: conversation` + refusal scoring pull in different
  directions (route wants "just chat", refusal metric wants a decline). Both can be
  satisfied only if the model chats *and* declines to answer from memory. Worth a
  sentence in README so a future reader does not treat a q082 non-refusal as a bug.
- **q018** `expected_route: hybrid` for a fact that exists only in meeting note m1 —
  reasonable, since the phrasing reads conversational and hybrid covers both stores.

No routing expectation in the sample is clearly wrong.

---

## (c) Question realism

**Verdict: PASS.**

Phrasing reads like a real person talking to a companion app: contractions, lower-case,
slang ("what's the most on-fire thing at work", "how much am I allowed to blow on
Portugal"), vague references ("the thing I'm cooking this weekend"). The unanswerable
hard negatives are exactly the kind of plausible follow-up a user would actually ask
(hotel name, airline, wifi password, manager's name, electricity bill amount).

Mildly stilted (minor, no change required):

- **q010** "what am I carrying myself during the move instead of letting the movers
  take it?" — long and self-explaining; a real user would more likely ask "what am I
  taking in the car?".
- **q036** "does the launch retro say anything about what marketing has to do?" —
  "the launch retro" as a named artifact is slightly product-tester phrasing.
- **q081** "where's the book club meeting after Thursday's?" — the elision is a touch
  awkward but acceptable.

---

## Coverage

Independent counts (counted item-by-item from `items[]`, not from `_meta.counts`):

| Deliverable (roadmap Step 1.4 / task) | Required | Independent count | Verdict |
|---|---|---|---|
| Every `category` present | 10 categories | conversation_recall 18, reminder 6, todo 6, meeting 6, schedule 7, paraphrase_recall 11, summary_request 3, conversation 4, action_request 5, unanswerable 20 = **86** | PASS |
| All 4 disambiguation tiers present | 1,2,3,4 | Tier 1 ~58, Tier 2 17, **Tier 3 only 2** (q049, q061), Tier 4 4 (q058-q060, q082) | PASS (Tier 3 thin) |
| Structured-data paraphrase recall | >= 10 | 11 labelled `paraphrase_recall` (q044-q054); **~4-6 fully lexically disjoint** from their source record | PASS WITH NOTES |
| Unanswerable / out-of-domain incl. hard negatives | >= 20 | **exactly 20** (q067-q086); 18 hard negatives + q082 out-of-domain + q072 (Dana phone) generic | PASS (zero margin) |
| Temporal (`is_temporal`) items | >= 10 | **17** (q001, q005, q013, q017, q024, q033, q034, q036, q037, q038, q039, q040, q042, q043, q056, q064, q071) | PASS |
| All action types via answerable-recall | reminder/todo/meeting/schedule/summary | reminder q019-q024, todo q025-q030, meeting q031-q036, schedule q037-q043, summary q055-q057 | PASS |
| All action types via `action_request` | reminder/todo/meeting/schedule/summary | q062 reminder, q063 todo, q065 meeting_note, q064 schedule, q066 summary_request | PASS |
| `_meta.counts` accurate | — | total 86 ✓, answerable 57 ✓, unanswerable 20 ✓, action_request 5 ✓, conversation 4 ✓, temporal 17 ✓, paraphrase_recall 11 ✓ | PASS |

**Verdict: PASS WITH NOTES.** All hard coverage requirements are met. Three soft spots:

1. **`paraphrase_recall` lexical-overlap strictness.** The roadmap defines the category
   as "queries where the user's phrasing does not share keywords with the stored field
   value". Under the loosest reading (stored *value* = the answer string) all 11 pass.
   Under a strict reading (any content word in the source record) only q046, q047,
   q048, q049 are clearly disjoint; q044, q045, q050, q051, q052, q053, q054 each
   share a topical noun with their source. The items are still meaningful paraphrase
   tests, but the "no keyword overlap" claim is over-stated for ~half of them.
   *Follow-up:* either tighten 3-4 questions to remove the shared noun, relabel those
   as `conversation_recall`, or document which reading of "keyword" is intended.
2. **Unanswerable count has zero margin** (exactly 20, requirement >= 20; smoke test
   only enforces >= 17). One item recategorised in future would breach the roadmap
   floor. Consider adding 2-3 more hard negatives for headroom.
3. **Tier 3 has only 2 items** (q049, q061). Enough to "be present" and tiers are
   report-only/never scored, but the per-tier breakdown in the report will be noisy.
   The roadmap only strictly requires Tiers 1-3; Tier 4 (4 items) is a bonus.

Also noted: the `AgenticActionType` enum value `"none"` is never exercised by any
item (not a roadmap requirement; the smoke test does not check it).

---

## Internal consistency

**Verdict: PASS.** Independently verified across all 86 items (not merely trusting
`test_harness_smoke.py`, though that test enforces the same three rules and is green):

- **Unique ids:** q001-q086, sequential, no duplicates, no gaps.
- **`answerable` == (`ground_truth` is not null):** true for q001-q057 (all have
  non-null GT), false for q058-q086 (all null GT). No violation.
- **`expected_route` is null iff `expected_retrieve_needed` is false:** null-route set
  = {q058, q059, q060, q061, q062, q063, q064, q065, q082} — exactly the
  `expected_retrieve_needed:false` set. q066 is `action_request` but
  `expected_retrieve_needed:true` with `route:hybrid` — consistent. Every other item
  has `retrieve_needed:true` + a non-null route.
- **`disambiguation_tier` in {1,2,3,4,null}:** all items conform; null only on
  unanswerable/action items where a tier does not apply (schema allows it).
- **`source_ref` sanity:** every answerable item's `source_ref` points at a real seed
  id (session sN, reminder rN, todo tN, meeting mN, schedule_item siN). Spot-checked
  the full sample — all resolve.

---

## Temporal-accuracy floor recommendation

**Recommendation: `temporal_accuracy.min = 0.45`.**

Verified independently against both real local `--calibrate` runs against
`llama3.1:8b` (35-item subset = every unanswerable item + every `is_temporal`
answerable item):

| Run | Report | `temporal_accuracy` | `n_temporal_scored` |
|---|---|---|---|
| A | `eval/results/eval_20260904T132621Z.json` | 0.500 (7/14) | 14 |
| B | `eval/results/eval_20260904T140807Z.json` | 0.643 (9/14) | 14 |

I recomputed both aggregates by hand from each report's per-item `scores.temporal`
rather than trusting the `aggregate` block, and they match exactly: of the 15
`is_temporal AND answerable` items (q001, q005, q013, q017, q024, q033, q034, q036,
q037, q038, q039, q040, q042, q043, q056), **q024** correctly scores `temporal: null`
in both runs (its GT "Water the plants, and pay the electricity bill" carries no
weekday/month/clock/ordinal token — see (a) above), leaving **14** scored items, each
worth 1/14 ≈ 7.14pp.

**Applying the roadmap rule** (README/line 783: floor = measured accuracy − 5pp,
confirmed by a second run holding the band): using the lower of the two runs,
`0.500 − 0.05 = 0.45`. Run A (0.500) and Run B (0.643) both clear it, Run B with
substantial margin, so the band holds. I confirm the coordinator's proposed **0.45**.

**Per-item detail** (14 scored items; miss = `scores.temporal: 0.0`):

| id | Run A | Run B | failure mode |
|---|---|---|---|
| q001, q013, q033, q037, q038, q040, q042 | 1.0 | 1.0 | consistent hits |
| q005 | 0.0 | 0.0 | agent routes `structured` to `todos:t6/t1`, `reminders:r1` — none contain "Saturday morning" (that's in session s2, semantic); genuine retrieval-routing miss, both runs |
| q017 | 0.0 | 0.0 | same pattern — `structured` route pulls schedule_items/meeting_notes/reminders that don't contain "March 3rd" (session s10, semantic); miss both runs |
| q036 | 0.0 | 0.0 | **not** a routing-to-wrong-chunk miss like the others — `retrieve_needed: false`, `confidence: 0.0`, `action_type: conversation`, zero chunks in *both* runs. The agent fails to recognize the question needs retrieval at all. Distinct, arguably more concerning failure mode than the others in this row. |
| q039 | 0.0 | 0.0 | Run A: wrong chunks retrieved (no `schedule_items:si3`) → refused. Run B: **`si3` *is* retrieved**, but the generated answer only says "Thursday" — drops "19th" and "7pm" — so this run's miss is a generation-completeness gap, not a retrieval miss. Two different failure modes landing on the same score. |
| q043 | 0.0 | 0.0 | identical chunk set both runs (`todos:t6/t1`, `schedule_items:si3`, `reminders:r2/r6`) — never retrieves `si4`/`si5`, the actual Friday-the-20th items; consistent retrieval-routing miss |
| q034 | 0.0 | 1.0 | flips: Run A's chunks omit `meeting_notes:m2` (the payments 1:1 note) → refusal; Run B's chunks include it → correct answer. Retrieval non-determinism, not a formatting bug. |
| q056 | 0.0 | 1.0 | flips: Run A retrieves only `meeting_notes:m2` (wrong meeting — that's the payments note, not the move) → refusal; Run B retrieves `s3`/`s1`/`s5` primary chunks plus `m2` and correctly surfaces "the 15th". Same non-determinism pattern as q034. |

This confirms the coordinator's characterization for 5 of the 7 always-miss/flip
items (q005, q017, q039-Run A, q043, q034, q056: wrong or missing structured/semantic
chunks). Two nuances worth recording for whoever picks up the retrieval-routing
follow-up: **q036** fails a step earlier (no retrieval attempted at all, confidence
collapses to 0) rather than retrieving the wrong record, and **q039** in Run B shows
that even a correct retrieval doesn't guarantee a complete answer (the model dropped
two of three required date/time tokens). Both are genuine pipeline weaknesses, not
eval-fixture bugs — no ground-truth or scoring-regex problem explains either.

**Timezone risk from the original review is resolved, confirmed empirically.** q037/
q038/q040/q042 (all schedule-time items whose `schedule_items` rows store
`...T08:00:00Z`/`T14:00:00Z`/`T09:00:00Z`) now score **1.0 in both runs** — the
generated answers correctly say "8am"/"2pm"/"9am"/"Monday the 16th" — after the
coordinator's fix surfacing schedule-item times/notes through structured search. No
further TZ-rendering concern for this floor.

**Caveat on statistical fragility (flag for follow-up, not a blocker):** n=14 is a
small denominator — each item is worth ~7.1pp, and the two observed runs already
span 14.3pp (0.500 → 0.643), noticeably wider than the roadmap's nominal "5pp
run-to-run stochasticity" assumption. A floor of 0.45 sits below both observed runs
(so it won't spuriously fail on normal rerun variance like the q034/q056 flips above)
while still being tight enough to catch a real regression — losing even one more
item than Run A's baseline (0.500 → 7/14 becomes 6/14 = 0.4286) would breach it. That
said, with only 14 items contributing, a couple of unlucky non-deterministic
retrievals (as seen with q034/q056) could plausibly swing a future run below 0.45
without a genuine model regression. Recommend tracking this gate's false-fail rate
once it runs in CI, and considering a 3rd confirmation run or a larger temporal-item
count in a future eval-set revision if it proves noisy in practice.

---

## Overall verdict

**SIGN-OFF WITH FOLLOW-UPS.**

The set is fit to gate releases on. Ground truth is accurate and fully traceable to
`seed_corpus.json`; there are no hard errors, no mislabelled answerable/unanswerable
items, and the internal-consistency invariants hold across all 86 items. Coverage meets
every hard roadmap deliverable. Question phrasing is realistic. The harness scoring
(`run_eval.py`) matches what the set claims.

Follow-ups (none blocking the 1.4b commit; all should be tracked):

1. **Tighten or document the `paraphrase_recall` category.** ~half the 11 items share
   a topical noun with their source record; the "no keyword overlap" claim is
   over-stated. Fix 3-4 questions or write down which reading of "keyword" is intended.
2. **Add headroom to the unanswerable set** — it sits at exactly the roadmap floor of 20.
3. **Reconsider q061 and q044/q045 routing expectations** (q061: conversation vs
   action_request; q044/q045: structured vs hybrid). Either widen the accepted set or
   reword.
4. **Anchor q024** ("reminders coming up this week") — give the harness a reference
   "today", or reword to a non-relative question, or drop `is_temporal`.
5. **Document the q082 tension** (route=conversation vs refusal metric) in README so a
   future non-refusal is not misread as a regression.
6. ~~Verify timezone rendering of schedule-item times~~ — **confirmed resolved** by the
   two real calibration runs: q037/q038/q040/q042 score 1.0/1.0 in both, with correctly
   rendered "8am"/"2pm"/"9am" answers.
7. **New, from real calibration data — the q076 hard negative earns its place.**
   Both runs answer q076 ("what's the deadline on Sam's blog post action item?") from
   `meeting_notes:m1` instead of refusing, even though the action item's `deadline` is
   `null` in the seed data. This is exactly the "answerable-looking negative" the item
   was designed to catch (see (a) above, where q076 was already confirmed a valid hard
   negative) — it is now a *real, reproduced* pipeline gap (the model treats "the
   record exists" as license to answer, rather than checking whether the specific
   field is populated), not a hypothetical. It is the sole reason `refusal_rate` sits
   at 0.95 instead of 1.0 in both runs. Track as a generation/prompt follow-up
   (teach the refusal condition to fire on a present-but-null field, not just an
   absent record) — separate from this eval-set review, but the set correctly
   surfaced it.
8. **Retrieval-routing weakness confirmed on temporal items** — q005, q017, q043 miss
   in both calibration runs, and q034/q056 flip between runs, because the router
   pulls the wrong (or no) structured/semantic chunks; q036 fails a step earlier
   (`retrieve_needed` collapses to `false`); q039 in Run B shows a correct retrieval
   with an incomplete generated answer. See the Temporal-accuracy floor section for
   detail. Real pipeline signal, not an eval-set defect — tracked as a product/retrieval
   follow-up, not a reason to revise the golden set.

Issue count: 0 hard ground-truth errors; 4 soft/under-anchored GT items (q024, q036,
q061, q082); 5 coverage/routing follow-ups; 1 timezone risk raised and now confirmed
resolved by real calibration data; 2 genuine pipeline weaknesses (q076 refusal gap,
temporal retrieval-routing) confirmed by two independent real runs as concrete
evidence the eval set does its job.
