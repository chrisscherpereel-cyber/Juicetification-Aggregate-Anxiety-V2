# Juicetification: Aggregate Anxiety

An individual, experiential **aggregate production planning** simulation built with
[Streamlit](https://streamlit.io). Students don't fill in a prepared spreadsheet —
they **build the plan themselves**: choose the data, pick the columns, select the
formulas, then compute a full 12‑month chase plan and level plan by hand, compare
them, and defend a recommendation.

You play the operations planning analyst for **Juicetification Inc.**, a small juice
bottler with a seasonal, spring/summer demand peak. Every student gets a **unique,
randomized scenario**, so answers can't be shared — the method is the same, the
numbers are yours.

---

## What students learn

- The difference between a **chase** strategy (match capacity to demand) and a
  **level** strategy (hold output steady, absorb swings with inventory/backorders).
- How to build an aggregate‑planning worksheet from scratch: required production,
  workforce, hiring/layoffs, inventory, backorders, and every cost category.
- How **inventory policy** (safety stock vs. none) and **worker model** (whole
  workers vs. partial "hours of capacity") change the correct plan.
- How to read a cost/benefit comparison and make — and justify — a managerial call.

## The 12 stages

| # | Stage | What happens |
|---|-------|--------------|
| 0 | Scenario Brief | Company, forecast, cost & capacity data |
| 1 | Understand the Problem | Frame the chase/level trade‑off |
| 2 | What Data Do You Need? | Select the real inputs (with distractors) |
| 3 | Choose Your Columns | Build the worksheet structure |
| 4 | Build the Formulas | Pick the correct formula for each column |
| 5 | Practice: Level & Chase Basics | Quick drills on the core calculations |
| 6 | Capacity | Compute bottles per worker / day & month |
| 7 | Chase Plan | Build the full 12‑month chase plan |
| 8 | Level Plan | Build the full 12‑month level plan |
| 9 | Compare Strategies | Interpret the metrics, answer guided questions |
| 10 | Design Challenge | Build a **hybrid** plan and try to beat both |
| 11 | Performance Report | Submission code, metrics, downloadable report |

## Pedagogy features

- **Live, cell‑level feedback** — wrong cells turn red, blanks amber, with a
  targeted "why is it wrong" explanation for each.
- **Excel‑style worksheet** — A/B/C columns, 1–12 rows, real formulas (`=D1*LABOR`,
  `=C1+D1-B1`), a point‑and‑click formula builder, and named brief‑data constants.
- **Predict‑then‑verify** — students commit a prediction before building, then see
  how it turned out.
- **"I'm stuck" that teaches** — reveals a full worked solution *and* then hands the
  student a fresh scenario so they rebuild it themselves.
- **Concept‑check "why" questions**, a **demand sensitivity** what‑if slider, and
  structured **reflection** prompts in the comparison stage.
- **Challenge metrics** — attempts, completion, and help usage are tracked per
  challenge and included in the report.

---

## Run it locally

Requires Python 3.9+.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens the app in your browser (usually `http://localhost:8501`).

## Deploy (optional)

The app is a single file with no database. It runs on
[Streamlit Community Cloud](https://streamlit.io/cloud): push this repo to GitHub,
point Streamlit Cloud at `app.py`, and share the URL.

## Scenario parameters (V1)

| Item | Value |
|------|-------|
| Beginning inventory | 2,400 bottles |
| Safety stock (if policy maintains one) | 2,400 bottles |
| Output rate | 5 bottles/hour × 8 hours/day × 25 days = 1,000 / worker / month |
| Regular labor | \$3,200 per full worker per month |
| Hiring / layoff | \$600 / \$900 per worker (\$75 / \$112.50 per hour‑per‑day) |
| Holding | \$0.25 / bottle / month |
| Backorder | \$1.50 / bottle / month |
| Overtime | up to +20%, \$4.50 / bottle |
| Starting workforce | 8 workers |

Demand is randomized per student in whole multiples of 500 with a clean annual
total, so the level rate and workforce math stay tidy while remaining unique.

## Instructor configuration (optional)

The app runs standalone with the defaults above and needs no setup. It is also
**Juicetification Director**‑ready: an instructor can override any scenario
parameter (costs, capacity, starting workforce, inventory policy) or pin a fixed
12‑month demand — without editing code — through URL query parameters.

- `?manifest=1` — the app emits its parameter schema as JSON and stops. The
  Director reads this to build a config form. No shared code is required.
- `?cfg=<base64 json>` — a self‑contained config link. The encoded values override
  the built‑in defaults; anything omitted keeps its default.
- `?game=<code>` / `?seed=<int>` — hooks for a shared config endpoint and a fixed
  per‑student scenario seed.

With **no** such parameter present, `resolve_config` returns the built‑in defaults
and the app behaves exactly as before. Configurable values live in
[`manifest.py`](manifest.py); [`juice_director.py`](juice_director.py) is the shared
loader (identical across every Director app) and validates, coerces, and clamps
incoming values to the schema. Named formula constants (`LABOR`, `HOLD`, `HIRE`, …)
derive from the resolved parameters, so a config change flows through the worksheet
formula engine too.

Example: set regular labor to \$4,000 and fix the demand curve —

```python
from juice_director import encode_cfg
encode_cfg({"regular_labor_cost": 4000,
            "forecast_demand": [12000,12500,15000,20000,26000,31000,
                                33000,28000,22000,17000,14000,11000]})
# → paste the string after ?cfg=  in the app URL
```

## Files

- `app.py` — the entire simulation (single file, no external data).
- `manifest.py` — the configurable parameter schema (used by the Director).
- `juice_director.py` — shared Director config loader (identical across apps).
- `requirements.txt` — Python dependencies.
- `Juicetification_Aggregate_Anxiety_Instructions.pdf` — one‑page student handout.
- `docs/` — supplementary material: the academic write‑up (`.docx`) and figures.
- `LICENSE` — MIT.

## License

Released under the MIT License — see [`LICENSE`](LICENSE).
