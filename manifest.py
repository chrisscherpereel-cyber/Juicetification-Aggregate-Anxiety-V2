# manifest.py — parameter schema for Aggregate Anxiety.
# The Director reads this (via ?manifest=1) to render an instructor config form.
# Mirrors app.py PARAMS, plus an optional 12-value forecast_demand override.
APP_KEY = "app"; NAME = "Aggregate Anxiety"; SCHEMA_VERSION = 1
MANIFEST = {"app_key": APP_KEY, "name": NAME, "schema_version": SCHEMA_VERSION, "params": {
  "beginning_inventory": {"type": "int", "default": 2400, "min": 0, "group": "Inventory", "label": "Beginning inventory"},
  "safety_stock": {"type": "int", "default": 2400, "min": 0, "group": "Inventory", "label": "Safety stock"},
  "max_inventory": {"type": "int", "default": 30000, "min": 0, "group": "Inventory", "label": "Max inventory"},
  "bottles_per_worker": {"type": "int", "default": 1000, "min": 1, "group": "Capacity", "label": "Bottles/worker/month"},
  "bottles_per_hour": {"type": "int", "default": 5, "min": 1, "group": "Capacity", "label": "Bottles/worker-hour"},
  "hours_per_day": {"type": "int", "default": 8, "min": 1, "max": 24, "group": "Capacity", "label": "Hours/day"},
  "working_days": {"type": "int", "default": 25, "min": 1, "max": 31, "group": "Capacity", "label": "Working days/month"},
  "starting_workforce": {"type": "int", "default": 8, "min": 0, "group": "Capacity", "label": "Starting workforce"},
  "regular_labor_cost": {"type": "int", "default": 3200, "min": 0, "group": "Costs", "label": "Regular labor $/worker/mo"},
  "hiring_cost": {"type": "int", "default": 600, "min": 0, "group": "Costs", "label": "Hiring $/worker"},
  "layoff_cost": {"type": "int", "default": 900, "min": 0, "group": "Costs", "label": "Layoff $/worker"},
  "holding_cost": {"type": "float", "default": 0.25, "min": 0, "group": "Costs", "label": "Holding $/bottle/mo"},
  "backorder_cost": {"type": "float", "default": 1.50, "min": 0, "group": "Costs", "label": "Backorder $/bottle/mo"},
  "overtime_pct": {"type": "float", "default": 0.20, "min": 0, "max": 1, "group": "Costs", "label": "Overtime cap (fraction)"},
  "overtime_cost": {"type": "float", "default": 4.50, "min": 0, "group": "Costs", "label": "Overtime $/bottle"},
  "subcontract_cost": {"type": "float", "default": 5.25, "min": 0, "group": "Costs", "label": "Subcontract $/bottle"},
  "forecast_demand": {"type": "list", "default": [], "group": "Demand", "label": "12-month demand (blank = random per student)"},
}}
