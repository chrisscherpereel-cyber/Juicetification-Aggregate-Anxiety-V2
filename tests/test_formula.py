import time

import pandas as pd
import pytest

from aggplan.formula import evaluate_grid, shift_refs, FormulaError, tokenize

COLS = ["Month", "Forecast Demand", "Beginning Inventory", "Regular Production", "Workers"]
CONST = {"LABOR": 3200, "RATE": 1000, "HOLD": 0.25}


def grid(cells, n=4):
    """cells: {(col, row_index): text}."""
    df = pd.DataFrame({c: [""] * n for c in COLS})
    df["Month"] = ["M%d" % i for i in range(n)]
    df["Forecast Demand"] = [100.0 * (i + 1) for i in range(n)]
    for (c, i), v in cells.items():
        df.at[i, c] = v
    return evaluate_grid(df, CONST)


def val(cells, col="Workers", i=0):
    r = grid(cells)
    return r.values[col][i], r.errors.get((col, i))


def test_numbers_and_arithmetic():
    assert val({("Workers", 0): "12"})[0] == 12
    assert val({("Workers", 0): "1,500"})[0] == 1500
    assert val({("Workers", 0): "$3,200"})[0] == 3200
    assert val({("Workers", 0): "=2+3*4"})[0] == 14
    assert val({("Workers", 0): "=(2+3)*4"})[0] == 20
    assert val({("Workers", 0): "=-3+5"})[0] == 2
    assert val({("Workers", 0): "=2^3"})[0] == 8
    assert val({("Workers", 0): "=1e3"})[0] == 1000          # not cell E3
    assert val({("Workers", 0): "=50%"})[0] == 0.5
    assert val({("Workers", 0): "=8×3200−100÷2"})[0] == 25550   # unicode operators from the UI text


def test_cell_refs_and_named_constants():
    cells = {("Regular Production", 0): "10500", ("Workers", 0): "=D1/RATE"}
    assert val(cells)[0] == 10.5
    assert val({("Workers", 0): "=B1*HOLD"})[0] == 25          # B = Forecast Demand
    assert val({("Workers", 0): "=b1+$B$1"})[0] == 200


def test_functions_taught_in_stage_4_work():
    assert val({("Workers", 0): "=MAX(0,5-7)"})[0] == 0
    assert val({("Workers", 0): "=MIN(4,2,9)"})[0] == 2
    assert val({("Workers", 0): "=ROUNDUP(10500/1000,0)"})[0] == 11
    assert val({("Workers", 0): "=ROUNDUP(10500/1000)"})[0] == 11
    assert val({("Workers", 0): "=ROUNDDOWN(10.9)"})[0] == 10
    assert val({("Workers", 0): "=ROUND(2.5)"})[0] == 3
    assert val({("Workers", 0): "=ABS(-4)"})[0] == 4
    assert val({("Workers", 0): "=SUM(B1:B3)"})[0] == 600
    assert val({("Workers", 0): "=roundup(2.1)"})[0] == 3       # case-insensitive


def test_roundup_exact_multiple_does_not_overshoot():
    assert val({("Workers", 0): "=ROUNDUP(10000/1000)"})[0] == 10


def test_division_by_zero_message():
    v, e = val({("Workers", 0): "=B1/0"})
    assert v is None and e.code == "DIV0" and "Division by zero" in e.message
    v, e = val({("Workers", 0): "=B1/D1"})          # D1 is blank
    assert e.code == "DIV0" and "D1 is blank" in e.message


def test_missing_reference_messages():
    v, e = val({("Workers", 0): "=Z1+5"})
    assert e.code == "REF" and "Z1" in e.message and "isn't in the worksheet" in e.message
    v, e = val({("Workers", 0): "=B99"})
    assert e.code == "REF" and "rows 1–4" in e.message
    v, e = val({("Workers", 0): "=A1+1"})
    assert e.code == "TYPE" and "month name" in e.message


def test_blank_reference_counts_as_zero_with_a_note():
    r = grid({("Workers", 0): "=D1+5"})
    assert r.values["Workers"][0] == 5
    assert any("D1 is blank" in n for n in r.notes[("Workers", 0)])


def test_circular_reference_detected_with_path():
    r = grid({("Workers", 0): "=D1+1", ("Regular Production", 0): "=E1+1"})
    for key in (("Workers", 0), ("Regular Production", 0)):
        assert r.errors[key].code == "CIRC"
    assert "→" in r.errors[("Workers", 0)].message
    r = grid({("Workers", 0): "=E1"})                      # self reference
    assert r.errors[("Workers", 0)].code == "CIRC"


def test_dependent_cell_reports_upstream_error():
    r = grid({("Workers", 0): "=B1/0", ("Regular Production", 0): "=E1+1"})
    e = r.errors[("Regular Production", 0)]
    assert e.code == "DEP" and "E1" in e.message


@pytest.mark.parametrize("text,code", [
    ("=2**3", "UNSUPPORTED"), ("=B1>3", "UNSUPPORTED"), ("=IF(1,2,3)", "UNSUPPORTED"),
    ("=LABER*2", "NAME"), ("=(1+2", "SYNTAX"), ("=1+", "SYNTAX"), ("=", "SYNTAX"),
    ("=1 2", "SYNTAX"), ("=\"abc\"", "SYNTAX"), ("=import os", "NAME"),
    ("=__import__('os')", "SYNTAX"), ("=MAX()", "ARGS"), ("=ABS(1,2)", "ARGS"),
    ("hello", "NAME"),
])
def test_unsupported_or_invalid_syntax_has_specific_codes(text, code):
    v, e = val({("Workers", 0): text})
    assert v is None and e is not None and e.code == code, (text, e)


def test_name_suggestion():
    _, e = val({("Workers", 0): "=LABER*2"})
    assert "LABOR" in e.message


def test_expression_and_computation_limits():
    t0 = time.time()
    # the legacy whitelist accepted this and hung the server
    v, e = val({("Workers", 0): "=9^9^9^9"})
    assert e.code == "LIMIT"
    assert val({("Workers", 0): "=" + "1+" * 200 + "1"})[1].code == "LIMIT"
    assert val({("Workers", 0): "=" + "(" * 40 + "1" + ")" * 40})[1].code == "LIMIT"
    assert val({("Workers", 0): "=10^300"})[1].code == "LIMIT"
    assert val({("Workers", 0): "=SUM(B1:B200)"})[1].code in ("LIMIT", "REF")
    assert time.time() - t0 < 2


def test_grid_level_work_limit_with_long_dependency_chain():
    n = 12
    df = pd.DataFrame({c: [""] * n for c in COLS})
    df["Month"] = ["M"] * n
    df["Forecast Demand"] = [1.0] * n
    for i in range(n):
        df.at[i, "Workers"] = "=" + "+".join(["D%d" % (i + 1)] * 5 + ["E%d" % (i if i else 1)])
    t0 = time.time()
    evaluate_grid(df, CONST)
    assert time.time() - t0 < 2


def test_tokenizer_rejects_double_star_even_when_nested():
    with pytest.raises(FormulaError) as e:
        tokenize("9**9**9")
    assert e.value.code == "UNSUPPORTED"


def test_shift_refs_fill_down():
    assert shift_refs("=D1*LABOR", 2) == "=D3*LABOR"
    assert shift_refs("=C1+D1-B1", 1) == "=C2+D2-B2"
    assert shift_refs("=$B$1+B1", 3) == "=$B$1+B4"
    assert shift_refs("=ROUNDUP(D1/RATE)", 1) == "=ROUNDUP(D2/RATE)"


def test_plain_text_formula_without_equals_is_tolerated_with_note():
    r = grid({("Workers", 0): "B1/2"})
    assert r.values["Workers"][0] == 50
    assert r.notes[("Workers", 0)]
