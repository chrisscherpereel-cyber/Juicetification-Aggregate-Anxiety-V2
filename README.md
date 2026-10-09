# Juicetification: Aggregate Anxiety (model 3.0)

An individual, experiential **aggregate production planning** simulation built with
[Streamlit](https://streamlit.io). Students don't fill in a prepared spreadsheet — they
**identify the data, choose the columns, pick the formulas, calculate a 12-month chase plan and
level plan themselves** (in the app, or in Excel), compare them, defend a recommendation in their
own words, and finish by **downloading a PDF completion report to upload to their LMS**.

You play the operations planning analyst for **Juicetification Inc.**, a small juice bottler with
a seasonal demand peak. Every student gets a reproducible, personal scenario, so numbers can't be
shared — the method is the same, the answers are yours.

> Version 3.0 is a restructuring and correction release. See [docs/AUDIT.md](docs/AUDIT.md) for
> what was verified as defective in V2 (and what was *not*), and
> [docs/LEARNING_OUTCOMES.md](docs/LEARNING_OUTCOMES.md) for how each change maps to a learning
> objective and how it could be evaluated. **No improvement in learning outcomes is claimed** —
> that needs a classroom study.

---

## Student workflow

Five groups in the sidebar; every stage shows **your task**, **what to do now**, overall
progress, a **Check my work** button, and — if the stage isn't complete — **why**.

| Group | Stages |
|-------|--------|
| 1 · Understand the scenario | Scenario brief · Frame the problem (optional) |
| 2 · Prepare the worksheet | What data? · Choose columns · Build formulas · Practice drills (optional) · Capacity |
| 3 · Build chase and level plans | Chase worksheet · Level worksheet (each can be done in Excel and imported) |
| 4 · Compare and improve | Compare plans · Hybrid design challenge (optional) · Advanced options (optional) |
| 5 · Submit and reflect | Recommendation + reflections · Download PDF report |

* Scenario data and the relevant formulas are one click away beside the worksheet.
* **Feedback is explicit by default.** Calculated values and formula errors update as you type, but
  ✔/✖ verdicts appear only after **Check my work** (set `feedback_mode=live` for the V2 behaviour).
  Every status uses a symbol *and* a word, never color alone; tables are real HTML tables with
  captions and header cells, and everything is reachable with the keyboard.
* Work is preserved when navigating. With server saving it also survives a refresh; without it, use
  the **recovery file** (sidebar) — a checksummed JSON download you can upload later.
* The worksheet uses real spreadsheet addressing: **row 1 = headings, January = row 2,** columns
  A–Q. In-app formulas and Excel formulas are therefore identical.

### Model rules shown to students

* **Inventory vs. backlog.** Physical inventory is never negative. Unfilled demand is *backlog*
  carried to the next month; arriving stock clears the **oldest orders first**, then ships the
  current month's demand. The worksheet has separate *Beginning Inventory* and *Beginning Backlog*
  columns (V2 displayed a negative "beginning inventory").
* **Safety stock is a planning target, not a hard floor.** A plan that maintains it aims to end every
  month with at least S bottles; required production = demand + S − net position. Customers are
  served first, so stock below S is shipped and the month is reported as *below safety stock*.
  Beginning inventory (B) and safety stock (S) are independent (V2 silently used B as the buffer
  while the level rate used S).
* **Terminal requirements.** December backlog ≤ `terminal_backlog_max` (default 0); December
  inventory ≥ the policy target (S if the plan maintains safety stock, otherwise 0).
* **Workers are paid for capacity.** `workers × bottles/worker/month`; production may be lower
  (*idle paid capacity*, shown). Fractional workers are **worker-equivalents / paid labor hours**
  ("Paid Labor Hours/Day" is the total across all workers — not one line's running time).
* **Service measures** (replacing the single "service level"): *on-time fulfillment %* (demand
  shipped in its due month), *months with shortages*, *months ending with backlog*, *ending
  backlog*, *demand fulfilled by year end*.
* **Costs and rounding.** Quantities are carried at full precision; money is shown to the cent and
  totals come from unrounded monthly values (reconciliation tested to < $0.01). Overtime and
  subcontracting are extra $/bottle on top of regular labor. The full rules are the docstring of
  [aggplan/engine.py](aggplan/engine.py).

## Hybrid design challenge

Quarterly workforce decisions (introductory) or an optional **monthly** table. Overtime uses the
configured `overtime_pct`/`overtime_cost`; the student **types quantities** or turns on a clearly
labelled **assistance** option that only fills shortages. Subcontracting (optional) uses
`subcontract_cost` and an explicit `subcontract_capacity`. A **feasibility panel** checks capacity,
overtime limit (and overtime only when regular capacity is full), subcontract limit, storage,
required on-time fulfillment, safety stock policy, and terminal backlog/inventory. Regular and
overtime production and all costs appear in the monthly detail and total. Chase and level are
**re-run under the hybrid's own assumptions** for a like-for-like comparison, and mismatched
assumptions are explained. A hybrid is **not promised to beat** the references. Attempts are saved
explicitly; the **best feasible** plan is kept separately from a **cheaper infeasible** one.

## Optional advanced exercises

Instructor-selectable `scenario_preset` (changes only parameters left at default):
`strong_seasonal`, `stability_favored`, `limited_flex`, `tight_storage`, `forecast_error`,
`perishable`. Also available by parameter:

* **Forecast error** (`forecast_error_pct`) — the student commits a plan using only the forecast,
  then sees it evaluated across 200 reproducible realized-demand draws they never see.
* **Perishability** (`shelf_life_months`) — inventory-age cohorts, oldest shipped first, explicit
  disposal bottles and cost. The worksheet's "above two months' demand" warning is relabelled a
  *heuristic*; real ageing is used only in this test.
* **Optimization benchmark** (needs SciPy) — a MILP using the same constraints as the student model
  (integer workers when whole-worker mode is used). Shown only **after submission** by default
  (`benchmark_reveal`: `after_submission` | `always` | `never`). The result is re-simulated by the
  student engine and labelled **proven optimal** only when the solver proves it; otherwise
  *feasible, not proven optimal*.

## Excel

Every plan stage has *Work in Excel instead*: download a workbook for **your** scenario (Read Me,
Scenario with units and named cells such as `LABOR`/`RATE`, blank Chase and Level templates, Hybrid
sheet, Column Guide), complete it, and upload it. The import validates the workbook against your
active scenario (seed/config/model version, headings, locked cells, text in numeric cells, Excel
errors) with actionable messages, then loads it into the **same** worksheet graded by the **same**
checker. Completed plans can be exported (values equal the engine's, with assumptions and
metric definitions). Limits: files are not opened in Excel by the test suite (see *Verification*).

## PDF completion report (replaces the completion code)

On **Submit and reflect → Download your PDF report**:

1. Every required activity must be complete for the final report (the page lists exactly what is
   missing). A **draft** can be downloaded any time; every page is marked DRAFT (banner,
   watermark, footer) and an unverified plan's numbers are never printed.
2. *Generate final report* builds a US-Letter PDF with the student's name/section, assignment,
   date-time **with timezone**, unique **attempt ID**, scenario seed, model version, resolved
   parameters, activity checklist, chase-vs-level comparison (cost categories, workforce, inventory
   and backlog, service measures, feasibility and terminal requirements), the selected strategy and
   **verbatim** justification and reflections, hybrid results (best feasible separated from
   infeasible attempts), neutral attempts/help usage, and a landscape appendix with the monthly
   worksheets. Plan cost is labelled *plan cost — not an academic grade*.
3. **Download PDF for LMS submission** → upload the PDF to your LMS assignment.
4. Only **after** the PDF exists is the completion recorded (attempt ID + snapshot hash + seed +
   model version + plan costs) in the encrypted store when configured. If recording fails the
   student is told and can retry, and **the PDF is unaffected**.

The file stays available across Streamlit reruns; if the work changes after generation the app says
the report is out of date. Filename: `Aggregate_Anxiety_<Last>_<First>_<AttemptID>.pdf`.
Characters the bundled font lacks (CJK, emoji, right-to-left) print as `□` with a notice — the
original text remains in the student's work.

## Run it

Python 3.9+.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Development / tests: `pip install -r requirements-dev.txt && python -m pytest tests -q`.

## Instructor configuration (optional)

Standalone practice needs **no setup**. Every parameter in [manifest.py](manifest.py) can be set by
the Juicetification Director form, or by URL: `?cfg=<base64 json>`, `?game=<code>`, `?seed=<int>`,
`?manifest=1` (emits the schema and stops). Added in 3.0, all optional with V2-compatible defaults:

| Parameter | Default | Meaning |
|---|---|---|
| `subcontract_capacity` | 5000 | bottles/month available for subcontracting |
| `service_requirement_pct` | 95 | required on-time fulfillment for hybrid feasibility (0 = none) |
| `terminal_backlog_max` | 0 | maximum December backlog |
| `scenario_preset` | standard | see *Optional advanced exercises* |
| `feedback_mode` | on_check | `on_check` or `live` |
| `benchmark_reveal` | after_submission | `after_submission` / `always` / `never` |
| `allow_replacement` | true | allow the "I'm stuck → replacement scenario" flow |
| `shelf_life_months`, `disposal_cost` | 0, 0.50 | perishability test |
| `forecast_error_pct` | 0 | enables the forecast-error exercise |
| `assignment_name`, `report_timezone` | …, server time | shown on the PDF (`America/Phoenix`, …) |

Validation never crashes the app: a demand list that isn't exactly 12 non-negative numbers is
ignored with a visible note; `bottles_per_worker` is derived from rate × hours × days if they
disagree. **Replacement scenarios** ("I'm stuck") are deterministic (`variant` counter): a fresh
reproducible demand pattern, or — when you fixed the demand curve — a seeded variant of that curve
(set `allow_replacement=false` to forbid it). Scenario lineage is saved and the report lists the
seed and variant in force.

## Saving, identity and integrity (optional)

Configured with the same secrets as the Director (`DB_ENCRYPTION_KEY` plus Dropbox credentials; see
`student_store.py`, unchanged and still a safe no-op when unset).

* **Versioned progress.** Each save holds the model version, schema, seed lineage, resolved
  parameters and a hash of the instructor configuration. A saved record is restored only if all are
  compatible; otherwise the student sees why, can **download the old record**, and chooses to start
  fresh. Nothing is silently restored or overwritten, and saving is paused when loading failed.
* **Save status.** The sidebar shows saved-at time or an actionable, secret-free failure message,
  with automatic retry; the recovery file always works.
* **Identity.** A `?sid=` is a label, not authentication. For graded use set
  `ASSIGNMENT_SIGNING_KEY` and mint per-student links:

  ```bash
  export ASSIGNMENT_SIGNING_KEY='long random secret'
  python tools/make_assignment_links.py --base-url https://your-app.streamlit.app \
         --game OPS301-F26 --roster roster.csv --expires 2026-12-20 > links.csv
  ```

  The app then accepts `?game=&sid=&tok=` only when the HMAC token matches; anything else gets a
  sign-in screen (with a practice-mode escape that records nothing). Without a key, legacy `?sid=`
  links still work but are labelled **not verified** in the app and on the report. The same key
  seals the report's integrity hash (`tools/make_assignment_links.py --verify-seal ATTEMPT HASH SEAL`).
* **Submission record.** `completion_code` = the unique attempt ID; `score` is left empty (plan
  cost is in `extra`, labelled as not a grade). The Director's roster keeps working.

## Architecture

```
app.py                 wiring: identity → restore → navigation → stage → autosave
aggplan/               no Streamlit import at module load; unit-testable
  scenario.py          Scenario (explicit parameters + demand), presets, replacement variants
  engine.py            simulate(), reference plans, metrics, feasibility (model rules in docstring)
  formula.py           safe formula parser/evaluator (no eval; limits; specific errors)
  worksheet.py         column layout (A–Q), grading
  text.py              instructional text generated from the active scenario
  hybrid.py  uncertainty.py  benchmark.py    advanced decisions and exercises
  persistence.py  identity.py                saving, versioning, recovery, signed links
  workflow.py  report.py  report_pdf.py      completion rules, snapshot, ReportLab PDF
  excel_io.py          template / import / export
aggplan_ui/            Streamlit screens
tools/                 make_assignment_links.py, make_handout.py
tests/                 pytest suite
```

Calculation functions take the scenario explicitly — no global `FORECAST` is swapped any more.
The formula evaluator supports `+ - * / ^`, parentheses, cell references, brief-data names and
`MAX MIN SUM ROUNDUP ROUNDDOWN ROUND ABS`, and reports missing references, circular references,
unsupported syntax and division by zero by cell, with limits on length, nesting and work.

## Verification (what was and was not checked)

`python -m pytest tests -q` — see the table in the delivery notes / run it yourself. Covered:
inventory and backlog conservation, backlog clearance, B ≠ S, whole and fractional workforce, zero
demand, cost reconciliation, overtime/subcontract limits, feasibility, perishability cohorts,
benchmark soundness (no random feasible plan beats the "optimal" cost), formula errors and limits,
Excel round trips, PDF content/labelling/special characters/long text/matching totals,
versioning, identity, storage failure, and Streamlit smoke runs of every stage. Visual checks of
the PDF and a manual browser check of the worksheet grid were done; the grid's keyboard behaviour
is Streamlit's. **Not verified:** workbooks were never opened in Excel or LibreOffice (only
re-read with openpyxl); a real Dropbox store and signed links were exercised with fakes only.

## Known limitations

* Without server saving, a browser refresh clears the session (use the recovery file).
* PDF text cannot show CJK/emoji/right-to-left scripts (shown as □).
* Instructor presets override only parameters left at their default.
* The benchmark ignores perishability and is a yardstick, not an answer key.
* The report seal does not prevent a student editing their own PDF; the stored record is authoritative.

## Files

`Juicetification_Aggregate_Anxiety_Instructions.pdf` (regenerate with `tools/make_handout.py`),
`docs/AUDIT.md`, `docs/LEARNING_OUTCOMES.md`, `LICENSE` (MIT), bundled DejaVu fonts with
`aggplan/fonts/LICENSE-DejaVu.txt`.

## License

MIT — see [`LICENSE`](LICENSE).
