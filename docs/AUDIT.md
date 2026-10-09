# Audit of Juicetification: Aggregate Anxiety V2 (pre-3.0)

Method: read all of `app.py` (2,579 lines), `manifest.py`, `juice_director.py`,
`student_store.py`; then ran the legacy engine and evaluator in isolation to confirm
suspected defects. **Confirmed** = reproduced against the legacy code.
**Proposed** = enhancement, not a defect.

Priority: P1 = wrong numbers / data loss / integrity; P2 = learning or usability
problem; P3 = maintainability / enhancement.

## A. Calculation engine (`app.py` L182-364)

| # | Finding | Status | Impact | Pri |
|---|---------|--------|--------|-----|
| A1 | `build_plan()` holds `beginning_inventory` as the maintained buffer; `level_rate()` targets `safety_stock`. With B=2,400, S=3,600 a "maintain" chase plan ends every month at 2,400, not 3,600. | Confirmed | Wrong plan whenever an instructor sets the two differently | P1 |
| A2 | Net position is shown as "Beginning Inventory": the legacy level plan displays −5,000 … −8,900 beginning *inventory* in Aug-Dec. | Confirmed | Physical stock can't be negative; blurs inventory vs. backlog | P1 |
| A3 | "Service level" = Σ max(0, demand − *cumulative* backlog) ÷ Σ demand. I verified algebraically and numerically (72.6 % in both) that this equals *on-time fulfillment under oldest-orders-first clearing* — so the number is not wrong, but it is unlabeled, implicit (derived from the cumulative backlog rather than tracked), and silently depends on that clearing rule. "Service" also hides that all demand may still be shipped late. | Confirmed (definition/tracking, not arithmetic) | Ambiguous metric, no explicit fulfillment tracking | P2 |
| A4 | No terminal inventory/backlog rule. `Ending inventory ok` only tests net position ≥ 0. | Confirmed | A plan may end the year with backlog and be called fine | P1 |
| A5 | Backlog does carry forward and later production clears it (carry goes negative, production offsets it). The mechanism is right; its *presentation* (A2) and metrics (A3) are not. | Confirmed OK | - | - |
| A6 | Workers = ceil(production ÷ rate), production independent of workers, so idle paid capacity exists. It is never displayed. | Confirmed | Hidden cost driver | P2 |
| A7 | "Hours/Day = workers × 8" is described as "hours the line runs". It is paid labor hours across all workers. | Confirmed (wording) | Misleading model of the plant | P2 |
| A8 | Production/workers are `round(x, 4)` inside the engine; no stated rounding rule for money. | Confirmed | Sub-cent drift, undefined reconciliation | P3 |
| A9 | `strategy_summaries_for_demand()` swaps global `FORECAST`/`TOTAL_DEMAND`. | Confirmed | Fragile, not thread-safe | P3 |
| A10 | `bottles_per_worker` is configurable independently of rate × hours × days; Stage 6 grades the product, the engine uses the parameter. | Confirmed (logic) | Contradictory data if config differs | P2 |
| A11 | `forecast_demand` with ≠12 values raises `KeyError('April')`. | Confirmed | App crash from a bad Director config | P1 |

## B. Hybrid design challenge (`app.py` L2395-2467)

| # | Finding | Status | Pri |
|---|---------|--------|-----|
| B1 | Overtime cap hard-coded 0.20; label hard-codes "+20 %, $4.50/bottle". | Confirmed | P1 |
| B2 | Overtime bottles are passed in as "Regular Production"; detail shows no regular/overtime split. | Confirmed | P2 |
| B3 | Overtime cost is added only to the headline; not in monthly rows. | Confirmed | P1 |
| B4 | `design_best` stores the cheapest total even when service < 99.9 % (cheaper-but-infeasible overwrites). | Confirmed | P1 |
| B5 | Hybrid ignores `max_inventory`, safety stock, terminal backlog; always whole workers and "use-first" inventory, while the target may be a partial-worker level plan. Not like-for-like. | Confirmed | P1 |
| B6 | Overtime is applied automatically, un-labelled. Subcontracting (parameter exists) unused. | Confirmed | P2 |
| B7 | Quarter labels hard-coded. | Confirmed | P3 |

## C. Student workflow / UI

| # | Finding | Status | Pri |
|---|---------|--------|-----|
| C1 | 12 flat stages; no progress summary, no consistent "check" action, no next step. | Confirmed | P2 |
| C2 | Cell colors appear *as you type* (right/wrong revealed before an attempt); color is the only wrong/ok signal in the worksheet. | Confirmed | P2 |
| C3 | Stage 1, 2, 4 and 3 answers (`s1q1`, `s2picks`, `s4_*_ans`, `cmp_*`, `q_*`, `cc*`, `dc_ot`, practice inputs, total-cost inputs) are **not** in `PROGRESS_KEYS`; they are lost on refresh/resume. | Confirmed | P1 |
| C4 | Typed `MAX()`/`ROUNDUP()` (taught in Stage 4) silently grade wrong. | Confirmed | P1 |
| C5 | Report prints the student's *typed* level rate/workforce (`level_prod`, `level_workers`), not verified values. | Confirmed | P1 |
| C6 | Brief-data panel, formula text, diagnostics, explanations hard-code 2,400 / $3,200 / 8 / 1,000 etc.; instructor config changes do not flow into the text. | Confirmed | P1 |
| C7 | `shuffled()` uses `hash(str)` (randomized per process) so option order changes after a restart/resume. | Confirmed | P3 |

## D. Formula evaluator (`evaluate_grid`, L782-836)

| # | Finding | Status | Pri |
|---|---------|--------|-----|
| D1 | `eval()` behind a whitelist regex that accepts `*`, so `9**9**9**9` is accepted and hangs the server (reproduced: the probe process had to be killed). | Confirmed | P1 |
| D2 | Division by zero, circular and unsupported syntax all become silent `None` ("wrong"). References to non-existent cells become `0` silently. | Confirmed | P2 |
| D3 | No `MAX/MIN/ROUND*` support (see C4). `1e5` parses as cell E5. | Confirmed | P2 |

## E. Persistence, identity, submission

| # | Finding | Status | Pri |
|---|---------|--------|-----|
| E1 | `autosave_progress()`/`restore_progress()` swallow every exception; the student never learns a save failed. | Confirmed | P1 |
| E2 | Saved progress carries no schema/model version, seed lineage, or config hash; it is restored silently under changed settings. | Confirmed | P1 |
| E3 | "I'm stuck → new scenario" draws `random.randint`. With an instructor-fixed `forecast_demand` the demand is unchanged (nothing to "prove"); with a fixed `?seed=` the new seed is not reproducible; the lineage is not recorded. | Confirmed | P2 |
| E4 | `?sid=` is trusted as identity: anyone can open another student's record or overwrite their completion. | Confirmed | P1 |
| E5 | Completion record = a code derived from `seed % 100000` + plan tag. Not unique per attempt, not verifiable; "score" is the plan cost. | Confirmed | P1 |
| E6 | Completion is recorded on every click of "Generate", before anything downloadable exists. | Confirmed | P2 |

## F. Maintainability

Single 2,579-line file mixing engine, UI, persistence, text. README says "V1" and
"single file". Fixed by the `aggplan/` package (see README "Architecture").

## Implementation order (as delivered)

1. Engine, scenario, formula evaluator, tests (A, D, B-engine parts).
2. Persistence/identity/save-status (E).
3. Workflow UI, Excel I/O (C).
4. Hybrid rebuild + advanced options (B, optional features).
5. PDF completion report and submission record (E5/E6 replaced).
