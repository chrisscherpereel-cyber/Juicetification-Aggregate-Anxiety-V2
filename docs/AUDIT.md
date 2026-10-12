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

## Post-3.0 evaluation (2026-10-12)

Reviewing the finished app specifically as a vehicle for learning aggregate planning **in
Excel** turned up two further problems, both now fixed.

| # | Finding | Status | Impact | Pri |
|---|---------|--------|--------|-----|
| G1 | The evaluator accepted only single-column ranges, so `=SUM(L2:P2)` — the ordinary Excel way to total a row of costs, and exactly what the workbook's own Column Guide describes — was marked wrong. Verified end to end: a workbook built with proper formulas failed 12 cells, while one of 180 hardcoded numbers passed. | Confirmed, fixed | Punished the better spreadsheet practice | P1 |
| G2 | Nothing recorded what the student typed. Grading compares values, so a hand-calculated plan was indistinguishable from a linked model — and the PDF appendix was generated from the engine's reference plan, not the student's entries, so an instructor could not tell them apart either. | Confirmed, fixed | Spreadsheet skill unassessable | P2 |

G1 was mitigated in practice for Excel submissions, because a file saved by real Excel carries
cached values and the importer falls back to them; the in-app path had no such fallback and
failed outright. Ranges now work down a column, across a row and over a block, and an
out-of-worksheet corner reports `REF` ("P99 isn't in the worksheet") rather than a size
complaint.

Addressed afterwards, once the course objective was confirmed as "basic Excel functions,
no Solver":

* `IF` and the comparisons `= < > <= >= <>` are supported (`IF` evaluates only the branch it
  takes). `AVERAGE` and `COUNT` were added and ignore blanks as Excel does. `IF(…>0,…,0)`
  and `MAX(0,…)` now both pass, which makes the contrast between them teachable.
* The workbook gained an **Excel Skills** sheet (fill-down, relative vs absolute with a
  self-checking `$` exercise, the function list, named cells, error values), conditional
  formatting with a written legend, and a demand-vs-production chart.

| # | Finding | Status | Impact | Pri |
|---|---------|--------|--------|-----|
| G3 | Every `=SUM(...)` written into a workbook went through a helper that forces `data_type="s"` (an anti-injection guard that is right for labels and anything user-derived). The Σ totals row on the Scenario, Chase, Level and Hybrid template sheets and on every exported plan sheet was therefore stored as an **inline string**: Excel displayed the literal text `=SUM(E2:E13)` instead of a total. Proved by writing a cell both ways and reading the raw `sheet1.xml`. | Confirmed, fixed | Every student and instructor workbook showed text where a total belonged | P1 |

G3 is exactly the class of defect the openpyxl-only test suite cannot catch: openpyxl reads
the value back as `"=SUM(...)"` whichever way it was stored, so only `data_type` or the raw
XML reveals it. Internal, known-safe formulas now use a separate `_formula()` helper; `_put()`
and its injection guard are unchanged for anything user-derived.
* Plan stages report the student's formula share and encourage formulas;
  `min_formula_share_pct` lets an instructor require one.

Still open, as deliberate scope choices rather than defects:
* No Excel **Solver** exercise — out of scope for this version by decision; the LP still
  lives in `aggplan/benchmark.py` and runs in Python.
* No Data Table or Scenario Manager; sensitivity remains an in-app slider.
* `COUNTIF` and `SUMPRODUCT` are still unsupported: `COUNTIF` needs string criteria such as
  `">0"`, which is a much larger parser change than the rest.

## Verified in real Excel (2026-10-12)

The workbook was finally opened in **Excel 16.0** via COM, recalculated, and exported to PDF
for visual inspection — the gap every previous round flagged as unclosable.

Confirmed working: the file opens with **no repair prompt**; Σ totals are real formulas and
compute (`=SUM(E2:E13)` → a number); conditional formatting fires on the right cells (amber on
the two months above a 12,000 storage limit, red on the five backorder months) and the data
bar renders; the chart draws; the Excel Skills check formulas evaluate. Excel's own
recalculation of a student-built level plan totalled **$489,375.00**, matching the app engine
exactly — an independent cross-check of the model through a second formula engine.

The `$` exercise was driven as a student would: typing `=E45*J44` and filling down gives
`OK` on the first row then `Not yet - check the $ signs` on all five below (the reference
drifts onto empty cells and the costs collapse to 0); `=E45*$J$44` gives `OK` six times.
The lesson works in practice, not just in principle.

Two defects that **only** rendering could reveal:

| # | Finding | Status | Impact | Pri |
|---|---------|--------|--------|-----|
| G4 | Wrapped legend text in columns S/T forced those worksheet rows taller. Rows 2–5 are also January–May, so the printed plan grid had five tall rows and seven short ones. | Confirmed, fixed | Visibly uneven worksheet | P3 |
| G5 | The skills-sheet tables wrapped only their last column, so the middle "What it does" column was clipped by the next column: "how many cells in a range contain a numbe", "picks one of two results depending on a te". | Confirmed, fixed | Students could not read what half the functions do | P2 |

G4 was fixed by moving the legend below the twelve month rows (anchored objects such as the
chart never affect row height, so only the text had to move); G5 by wrapping every cell of
those tables rather than only the last. Both were invisible to openpyxl, which reports cell
values and never lays anything out.

