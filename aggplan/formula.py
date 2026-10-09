"""Safe worksheet-formula evaluator (replaces the legacy regex + eval() approach).

Supported:  numbers (1500, 2.5, 1e5, 12%), cell references (E5, $E$5), ranges inside
functions (SUM(E1:E3)), brief-data names (LABOR, RATE, …), + - * / ^, parentheses, unary
minus, and the functions MAX MIN SUM ROUNDUP ROUNDDOWN ROUND ABS.
Not supported (reported with a specific message): ** , comparisons, text, IF and other
functions, references to other sheets.

Nothing is ever passed to eval(). Limits keep a hostile or accidental expression from
hanging a class server: expression length, token count, nesting depth, work per cell and
per grid, and result magnitude.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

MAX_LEN = 200
MAX_TOKENS = 120
MAX_DEPTH = 12
MAX_NODES_PER_CELL = 400
MAX_NODES_PER_GRID = 40000
MAX_ABS = 1e12

FUNCTIONS = {"MAX", "MIN", "SUM", "ROUNDUP", "ROUNDDOWN", "ROUND", "ABS"}
_ONE_ARG = {"ABS"}
_TWO_ARG = {"ROUND", "ROUNDUP", "ROUNDDOWN"}

_NORMALIZE = {"×": "*", "÷": "/", "−": "-", "–": "-", "—": "-", " ": " ",
              "“": '"', "”": '"'}


class FormulaError(Exception):
    """code: SYNTAX, UNSUPPORTED, NAME, REF, TYPE, DIV0, CIRC, DEP, LIMIT, ARGS."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self):
        return self.message


_TOKEN_RE = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
     |(?P<ident>\$?[A-Za-z_][A-Za-z0-9_]*\$?\d*)
     |(?P<pow2>\*\*)
     |(?P<op>[-+*/^(),:%])
     |(?P<cmp><=|>=|<>|[<>=])
     |(?P<other>.)
    )""", re.X)
_CELL_RE = re.compile(r"^\$?([A-Za-z]{1,2})\$?(\d+)$")
NUM_PLAIN_RE = re.compile(r"^[-+]?\$?\s*\d[\d,]*(\.\d+)?%?$|^[-+]?\$?\s*\.\d+$|^[-+]?\d+(\.\d+)?[eE][+-]?\d+$")


def normalize(text: str) -> str:
    s = str(text)
    for k, v in _NORMALIZE.items():
        s = s.replace(k, v)
    return s.strip()


def tokenize(src: str) -> List[Tuple[str, str]]:
    toks: List[Tuple[str, str]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind is None:
            continue
        if kind == "pow2":
            raise FormulaError("UNSUPPORTED", "'**' isn't supported — use ^ for a power "
                               "(e.g. =E1^2).")
        if kind == "cmp":
            raise FormulaError("UNSUPPORTED", f"Comparison '{val}' isn't supported here — "
                               "use MAX(0, …) or MIN(…) for floors and caps.")
        if kind == "other":
            if val.strip() == "":
                continue
            hint = " Text and quotes aren't allowed in formulas." if val in "\"'" else ""
            raise FormulaError("SYNTAX", f"Unexpected character '{val}'.{hint}")
        toks.append((kind, val))
        if len(toks) > MAX_TOKENS:
            raise FormulaError("LIMIT", f"Formula is too complex (more than {MAX_TOKENS} pieces).")
    return toks


# --------------------------------------------------------------------------- #
# Parser -> small AST of tuples
# --------------------------------------------------------------------------- #
class _Parser:
    def __init__(self, toks, constants):
        self.t, self.i, self.constants = toks, 0, constants
        self.depth = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, val):
        k, v = self.take()
        if v != val:
            raise FormulaError("SYNTAX", f"Expected '{val}'" + (f" but found '{v}'." if v else
                               " — the formula ends too early (missing closing parenthesis?)."))

    def parse(self):
        if not self.t:
            raise FormulaError("SYNTAX", "The formula is empty after '='.")
        node = self.expr()
        k, v = self.peek()
        if k is not None:
            raise FormulaError("SYNTAX", f"Unexpected '{v}' — operators need a value on both "
                               "sides (did you forget +, −, × or ÷ between two values?).")
        return node

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError("LIMIT", f"Formula is nested too deeply (more than {MAX_DEPTH} "
                               "levels of parentheses/functions).")

    def expr(self):
        node = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.power()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.power())
        return node

    def power(self):
        node = self.unary()
        while self.peek()[1] == "^":
            self.take()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        k, v = self.peek()
        if v in ("-", "+") and k == "op":
            self.take()
            self._enter()
            node = self.unary()
            self.depth -= 1
            return ("neg", node) if v == "-" else node
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek()[1] == "%":
            self.take()
            node = ("bin", "*", node, ("num", 0.01))
        return node

    def primary(self):
        k, v = self.take()
        if k == "num":
            try:
                return ("num", float(v))
            except ValueError:
                raise FormulaError("SYNTAX", f"'{v}' isn't a valid number.")
        if v == "(" and k == "op":
            self._enter()
            node = self.expr()
            self.expect(")")
            self.depth -= 1
            return node
        if k == "ident":
            return self.ident(v)
        if k is None:
            raise FormulaError("SYNTAX", "The formula ends early — a value is missing after "
                               "the last operator.")
        raise FormulaError("SYNTAX", f"Unexpected '{v}' — a value (number, cell or name) was "
                           "expected here.")

    def ident(self, v):
        nxt = self.peek()[1]
        if nxt == "(":
            name = v.upper()
            if name not in FUNCTIONS:
                close = difflib.get_close_matches(name, sorted(FUNCTIONS), 1, 0.6)
                extra = f" Did you mean {close[0]}?" if close else (
                    " Supported: " + ", ".join(sorted(FUNCTIONS)) + ".")
                raise FormulaError("UNSUPPORTED", f"Function '{v}' isn't supported.{extra}")
            self.take()
            self._enter()
            args = []
            if self.peek()[1] != ")":
                args.append(self.arg())
                while self.peek()[1] == ",":
                    self.take()
                    args.append(self.arg())
            self.expect(")")
            self.depth -= 1
            if name in _ONE_ARG and len(args) != 1:
                raise FormulaError("ARGS", f"{name} needs exactly 1 value, got {len(args)}.")
            if name in _TWO_ARG and len(args) not in (1, 2):
                raise FormulaError("ARGS", f"{name} needs a value and optional number of "
                                   f"decimals, got {len(args)} arguments.")
            if name in {"MAX", "MIN", "SUM"} and not args:
                raise FormulaError("ARGS", f"{name} needs at least one value.")
            return ("fn", name, args)
        m = _CELL_RE.match(v)
        if m:
            return ("cell", m.group(1).upper(), int(m.group(2)))
        key = v.upper()
        if key in self.constants:
            return ("const", key)
        pool = sorted(set(self.constants) | FUNCTIONS)
        close = difflib.get_close_matches(key, pool, 1, 0.6)
        extra = f" Did you mean {close[0]}?" if close else ""
        raise FormulaError("NAME", f"Unknown name '{v}'.{extra}")

    def arg(self):
        """An argument: an expression, or a range A1:A3 (only meaningful in SUM/MAX/MIN)."""
        node = self.expr()
        if self.peek()[1] == ":":
            self.take()
            end = self.expr()
            if node[0] != "cell" or end[0] != "cell":
                raise FormulaError("SYNTAX", "A range must look like E1:E3.")
            return ("range", node, end)
        return node


# --------------------------------------------------------------------------- #
# Grid evaluation
# --------------------------------------------------------------------------- #
@dataclass
class GridResult:
    values: Dict[str, List[Optional[float]]]
    errors: Dict[Tuple[str, int], FormulaError] = field(default_factory=dict)
    notes: Dict[Tuple[str, int], List[str]] = field(default_factory=dict)
    raw: Dict[Tuple[str, int], str] = field(default_factory=dict)

    def error_message(self, col: str, i: int) -> str:
        e = self.errors.get((col, i))
        return e.message if e else ""


def column_letter(idx: int) -> str:
    return chr(ord("A") + idx)


def letters_for(columns) -> Dict[str, str]:
    """{'A': first column name, 'B': …}"""
    return {column_letter(i): c for i, c in enumerate(columns)}


def _is_blank(raw) -> bool:
    return raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == ""


def evaluate_grid(df: pd.DataFrame, constants: Dict[str, float],
                  fixed_numeric: Tuple[str, ...] = ("Forecast Demand",),
                  text_cols: Tuple[str, ...] = ("Month",), row_base: int = 1) -> GridResult:
    """Evaluate every cell of the worksheet. `constants` are the brief-data names.
    `row_base` is the spreadsheet row number of the first data row (the app and the Excel
    template use 2: row 1 holds the column headings, January is row 2)."""
    df = df.reset_index(drop=True)
    constants = {k.upper(): float(v) for k, v in constants.items()}
    nrows = len(df)
    letters = letters_for(df.columns)
    last_letter = column_letter(len(df.columns) - 1)
    cache: Dict[Tuple[int, str], Tuple[Optional[float], Optional[FormulaError]]] = {}
    notes: Dict[Tuple[str, int], List[str]] = {}
    state = {"nodes": 0}
    stack: List[Tuple[int, str]] = []

    def cell_name(r, c):
        return f"{[L for L, n in letters.items() if n == c][0]}{r + row_base}"

    def tick(cell_nodes):
        state["nodes"] += 1
        cell_nodes[0] += 1
        if cell_nodes[0] > MAX_NODES_PER_CELL:
            raise FormulaError("LIMIT", "Formula needs too much work to evaluate; simplify it.")
        if state["nodes"] > MAX_NODES_PER_GRID:
            raise FormulaError("LIMIT", "The worksheet needs too much work to evaluate; "
                               "simplify your formulas.")

    def check_num(x):
        if x != x or abs(x) == float("inf") or abs(x) > MAX_ABS:
            raise FormulaError("LIMIT", "The result is too large (or not a number) — "
                               "check the formula for a mistake.")
        return x

    def lookup(col_letter, rown, here):
        cname = letters.get(col_letter)
        ref = f"{col_letter}{rown}"
        idx = rown - row_base
        if cname is None or idx < 0 or idx >= nrows:
            raise FormulaError("REF", f"{ref} isn't in the worksheet (columns A–{last_letter}, "
                               f"rows {row_base}–{row_base + nrows - 1}"
                               + (", row 1 holds the headings)." if row_base > 1 else ")."))
        if cname in text_cols:
            raise FormulaError("TYPE", f"{ref} holds a month name, not a number.")
        res = eval_cell(idx, cname)
        if res[1] is not None:
            if res[1].code == "CIRC":
                if stack and stack[-1] in getattr(res[1], "cycle", ()):
                    raise res[1]                    # this cell is itself part of the cycle
                raise FormulaError("DEP", f"{ref} is part of a circular reference.")
            raise FormulaError("DEP", f"{ref} has an error ({res[1].message.rstrip('.')}) — "
                               "fix that cell first.")
        if res[0] is None:
            notes.setdefault(here, []).append(f"{ref} is blank (counted as 0)")
            return 0.0, ref
        return res[0], ref

    def ev(node, here, cn):
        tick(cn)
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "const":
            return constants[node[1]]
        if kind == "cell":
            return lookup(node[1], node[2], here)[0]
        if kind == "neg":
            return -ev(node[1], here, cn)
        if kind == "bin":
            _, op, a, b = node
            x, y = ev(a, here, cn), ev(b, here, cn)
            if op == "+":
                return check_num(x + y)
            if op == "-":
                return check_num(x - y)
            if op == "*":
                return check_num(x * y)
            if op == "/":
                if y == 0:
                    blanks = [n for n in notes.get(here, []) if "blank" in n]
                    why = (f" ({blanks[-1].split(' is blank')[0]} is blank)" if blanks else "")
                    raise FormulaError("DIV0", "Division by zero — the number you divide by "
                                       f"is 0{why}.")
                return check_num(x / y)
            if op == "^":
                try:
                    if abs(y) > 64 or (abs(x) > 1 and abs(y) * math.log10(abs(x)) > 12):
                        raise OverflowError
                    return check_num(float(x) ** y)
                except (OverflowError, ZeroDivisionError, ValueError):
                    raise FormulaError("LIMIT", "That power is too large (or undefined).")
        if kind == "fn":
            return call(node[1], node[2], here, cn)
        if kind == "range":
            raise FormulaError("SYNTAX", "A range like E1:E3 can only be used inside "
                               "SUM, MAX or MIN.")
        raise FormulaError("SYNTAX", "Unrecognized formula element.")

    def expand(arg, here, cn):
        if arg[0] != "range":
            return [ev(arg, here, cn)]
        (_, c1, r1), (_, c2, r2) = arg[1], arg[2]
        if c1 != c2:
            raise FormulaError("SYNTAX", "Ranges must stay in one column, e.g. E1:E3.")
        lo, hi = sorted((r1, r2))
        if hi - lo > 50:
            raise FormulaError("LIMIT", "Range is too long.")
        out = []
        for rr in range(lo, hi + 1):
            tick(cn)
            out.append(lookup(c1, rr, here)[0])
        return out

    def call(name, args, here, cn):
        if name in ("MAX", "MIN", "SUM"):
            vals = [v for a in args for v in expand(a, here, cn)]
            return check_num({"MAX": max, "MIN": min, "SUM": sum}[name](vals))
        vals = [ev(a, here, cn) for a in args]
        if name == "ABS":
            return abs(vals[0])
        digits = int(vals[1]) if len(vals) > 1 else 0
        if abs(digits) > 6:
            raise FormulaError("ARGS", "Use between −6 and 6 decimal places.")
        f = 10.0 ** digits
        x = vals[0]
        if name == "ROUNDUP":
            return check_num(math.copysign(math.ceil(abs(x) * f - 1e-9) / f, x))
        if name == "ROUNDDOWN":
            return check_num(math.copysign(math.floor(abs(x) * f + 1e-9) / f, x))
        return check_num(math.floor(x * f + 0.5) / f if x >= 0 else -math.floor(-x * f + 0.5) / f)

    def eval_cell(r, cname):
        key = (r, cname)
        if key in cache:
            return cache[key]
        if key in stack:
            i = stack.index(key)
            path = " → ".join(cell_name(*k) for k in stack[i:] + [key])
            err = FormulaError("CIRC", f"Circular reference: {path}. A cell can't depend on "
                               "itself, directly or through other cells.")
            err.cycle = set(stack[i:])
            raise err
        stack.append(key)
        here = (cname, r)
        try:
            cache[key] = compute(r, cname, here)
        except FormulaError as e:
            cache[key] = (None, e)
        finally:
            stack.pop()
        return cache[key]

    def compute(r, cname, here):
        raw = df.at[r, cname]
        if cname in fixed_numeric:
            try:
                return float(raw), None
            except (TypeError, ValueError):
                return None, FormulaError("TYPE", "Demand must be a number.")
        if _is_blank(raw):
            return None, None
        s = normalize(raw)
        if len(s) > MAX_LEN:
            raise FormulaError("LIMIT", f"Formula is longer than {MAX_LEN} characters.")
        is_formula = s.startswith("=")
        if is_formula:
            s = s[1:]
        elif NUM_PLAIN_RE.match(s):
            t = s.replace(",", "").replace("$", "").replace(" ", "")
            pct = t.endswith("%")
            return check_num(float(t.rstrip("%")) / (100 if pct else 1)), None
        else:
            notes.setdefault(here, []).append("Treated as a formula; in Excel, start formulas with =")
        s = s.replace("$", "") if not is_formula else s
        if len(s) > MAX_LEN:
            raise FormulaError("LIMIT", f"Formula is longer than {MAX_LEN} characters.")
        toks = tokenize(s)
        ast = _Parser(toks, constants).parse()
        cn = [0]
        return check_num(ev(ast, here, cn)), None

    values: Dict[str, List[Optional[float]]] = {}
    errors: Dict[Tuple[str, int], FormulaError] = {}
    raws: Dict[Tuple[str, int], str] = {}
    for c in df.columns:
        col_vals: List[Optional[float]] = []
        for i in range(nrows):
            if c in text_cols:
                col_vals.append(None)
                continue
            v, e = eval_cell(i, c)
            col_vals.append(v)
            if e is not None:
                errors[(c, i)] = e
            raws[(c, i)] = "" if _is_blank(df.at[i, c]) else str(df.at[i, c]).strip()
        values[c] = col_vals
    return GridResult(values=values, errors=errors, notes=notes, raw=raws)


# --------------------------------------------------------------------------- #
# Fill-down helper
# --------------------------------------------------------------------------- #
_SHIFT_RE = re.compile(r"(?<![A-Za-z0-9_.])(\$?)([A-Za-z]{1,2})(\$?)(\d+)(?![A-Za-z0-9_(])")


def shift_refs(formula: str, delta: int) -> str:
    """Move every relative row reference down by `delta` (absolute $rows stay put)."""
    def sub(m):
        col_abs, col, row_abs, row = m.groups()
        new = int(row) if row_abs else int(row) + delta
        return f"{col_abs}{col}{row_abs}{new}"
    return _SHIFT_RE.sub(sub, str(formula))
