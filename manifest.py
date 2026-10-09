# manifest.py — parameter schema for Aggregate Anxiety.
# The Director reads this (via ?manifest=1) to render an instructor config form.
# Every parameter has a default, so an app opened with no config behaves as the
# standalone practice version. Parameters added in 3.0 are all OPTIONAL and default
# to the V2 behaviour (or to "off"), so existing Director configs keep working.
APP_KEY = "app"; NAME = "Aggregate Anxiety"; SCHEMA_VERSION = 1
MODEL_VERSION = "3.0.0"   # bump when the calculation model or saved-progress format changes

_PRESETS = ["standard", "strong_seasonal", "stability_favored", "limited_flex",
            "tight_storage", "forecast_error", "perishable"]

MANIFEST = {"app_key": APP_KEY, "name": NAME, "schema_version": SCHEMA_VERSION,
            "model_version": MODEL_VERSION, "params": {
  "beginning_inventory": {"type": "int", "default": 2400, "min": 0, "group": "Inventory", "label": "Beginning inventory"},
  "safety_stock": {"type": "int", "default": 2400, "min": 0, "group": "Inventory", "label": "Safety stock (planning target when a plan maintains one)"},
  "max_inventory": {"type": "int", "default": 30000, "min": 0, "group": "Inventory", "label": "Max inventory (storage capacity)"},
  "bottles_per_worker": {"type": "int", "default": 1000, "min": 1, "group": "Capacity", "label": "Bottles/worker/month (must equal rate x hours x days; derived if not)"},
  "bottles_per_hour": {"type": "int", "default": 5, "min": 1, "group": "Capacity", "label": "Bottles/worker-hour"},
  "hours_per_day": {"type": "int", "default": 8, "min": 1, "max": 24, "group": "Capacity", "label": "Paid hours/day per full worker"},
  "working_days": {"type": "int", "default": 25, "min": 1, "max": 31, "group": "Capacity", "label": "Working days/month"},
  "starting_workforce": {"type": "int", "default": 8, "min": 0, "group": "Capacity", "label": "Starting workforce"},
  "regular_labor_cost": {"type": "int", "default": 3200, "min": 0, "group": "Costs", "label": "Regular labor $/worker/mo"},
  "hiring_cost": {"type": "int", "default": 600, "min": 0, "group": "Costs", "label": "Hiring $/worker"},
  "layoff_cost": {"type": "int", "default": 900, "min": 0, "group": "Costs", "label": "Layoff $/worker"},
  "holding_cost": {"type": "float", "default": 0.25, "min": 0, "group": "Costs", "label": "Holding $/bottle/mo"},
  "backorder_cost": {"type": "float", "default": 1.50, "min": 0, "group": "Costs", "label": "Backorder $/bottle/mo"},
  "overtime_pct": {"type": "float", "default": 0.20, "min": 0, "max": 1, "group": "Costs", "label": "Overtime cap (fraction of regular capacity)"},
  "overtime_cost": {"type": "float", "default": 4.50, "min": 0, "group": "Costs", "label": "Overtime $/bottle (extra, on top of regular labor)"},
  "subcontract_cost": {"type": "float", "default": 5.25, "min": 0, "group": "Costs", "label": "Subcontract $/bottle"},
  "subcontract_capacity": {"type": "int", "default": 5000, "min": 0, "group": "Capacity", "label": "Subcontract capacity (bottles/month)"},
  "service_requirement_pct": {"type": "float", "default": 95.0, "min": 0, "max": 100, "group": "Policy", "label": "Required on-time fulfillment % (hybrid feasibility; 0 = none)"},
  "terminal_backlog_max": {"type": "int", "default": 0, "min": 0, "group": "Policy", "label": "Max backlog allowed at end of December"},
  "scenario_preset": {"type": "str", "default": "standard", "choices": _PRESETS, "group": "Scenario", "label": "Scenario preset (changes only parameters left at default)"},
  "feedback_mode": {"type": "str", "default": "on_check", "choices": ["on_check", "live"], "group": "Pedagogy", "label": "Worksheet feedback: on_check (after 'Check my work') or live (as you type)"},
  "benchmark_reveal": {"type": "str", "default": "after_submission", "choices": ["after_submission", "always", "never"], "group": "Pedagogy", "label": "Optimization benchmark visibility"},
  "allow_replacement": {"type": "bool", "default": True, "group": "Pedagogy", "label": "Allow 'I'm stuck' replacement scenario"},
  "shelf_life_months": {"type": "int", "default": 0, "min": 0, "max": 12, "group": "Advanced", "label": "Shelf life in months (0 = not perishable; used by the perishability test)"},
  "disposal_cost": {"type": "float", "default": 0.50, "min": 0, "group": "Advanced", "label": "Disposal $/spoiled bottle"},
  "forecast_error_pct": {"type": "float", "default": 0.0, "min": 0, "max": 1, "group": "Advanced", "label": "Forecast error (monthly std-dev as fraction; 0 = off)"},
  "autosave_seconds": {"type": "float", "default": 5.0, "min": 0, "max": 120, "group": "Performance", "label": "Autosave debounce (s) - rapid edits coalesce into one upload"},
  "autosave_flush_seconds": {"type": "float", "default": 20.0, "min": 0, "max": 300, "group": "Performance", "label": "Background flush of pending saves (s; 0 = off)"},
  "assignment_name": {"type": "str", "default": "Aggregate Anxiety: Aggregate Production Planning", "group": "Report", "label": "Assignment name shown on the PDF report"},
  "report_timezone": {"type": "str", "default": "", "group": "Report", "label": "Report timezone (IANA name, e.g. America/Phoenix; blank = server time)"},
  "forecast_demand": {"type": "list", "default": [], "group": "Demand", "label": "12-month demand (blank = random per student)"},
}}
