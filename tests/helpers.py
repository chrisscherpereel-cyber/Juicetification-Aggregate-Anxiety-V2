from aggplan.scenario import resolve_scenario


def scn(demand=None, seed=7, **params):
    """A scenario with optional fixed demand and parameter overrides."""
    cfg = dict(params)
    if demand is not None:
        cfg["forecast_demand"] = list(demand)
    return resolve_scenario(cfg, seed)


FLAT = [10000] * 12
SEASONAL = [7000, 6000, 7000, 11500, 13000, 16500, 18500, 14000, 10500, 9000, 7000, 6000]
