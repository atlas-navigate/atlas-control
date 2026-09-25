"""
The one arithmetic evaluator Atlas uses.

Two things in Atlas evaluate math written by someone else: the calculator
page (/api/calculator, typed by whoever is on the network) and Ray's
[CALC: …] tags (written by the language model, whose output can be steered by
anything in its prompt — including a Meshtastic message from a stranger).
Both go through evaluate(), which:

* parses the expression with ast and walks it against a strict allowlist:
  numeric literals, + - * / // % **, unary +/-, and calls to *named*
  functions from an explicit registry. Attributes, subscripts, lambdas,
  comprehensions, keyword arguments, starred arguments, strings, names not in
  the registry — anything that could reach Python objects — are rejected
  before a single node is evaluated;
* bounds the work: expression length, parenthesis nesting, AST size, number
  of evaluation steps, integer bit-length (checked *before* a power or product
  is computed, so 9**9**9**9 is refused instead of eating the CPU), factorial
  input and float magnitude.

eval() is never used.
"""
import ast
import math

MAX_LENGTH = 300
MAX_NESTING = 32
MAX_NODES = 200
MAX_STEPS = 1000
MAX_INT_BITS = 4096
MAX_FLOAT = 1e300
MAX_FACTORIAL = 170


class MathError(ValueError):
    """The expression is not allowed or cannot be computed safely."""


class Func:
    """A callable exposed to expressions, with its accepted argument count."""

    def __init__(self, fn, min_args=1, max_args=None, check=None):
        self.fn = fn
        self.min_args = min_args
        self.max_args = min_args if max_args is None else max_args
        self.check = check

    def __call__(self, args):
        if not (self.min_args <= len(args) <= self.max_args):
            raise MathError("wrong number of arguments")
        if self.check:
            self.check(*args)
        return self.fn(*args)


def _require(cond, message):
    if not cond:
        raise MathError(message)


def _int_arg(x):
    if isinstance(x, float):
        _require(x.is_integer(), "expected a whole number")
        x = int(x)
    return x


def _factorial(n):
    n = _int_arg(n)
    _require(0 <= n <= MAX_FACTORIAL, f"factorial is limited to 0..{MAX_FACTORIAL}")
    return math.factorial(n)


def _round(x, ndigits=None):
    if ndigits is None:
        return round(x)
    ndigits = _int_arg(ndigits)
    _require(-20 <= ndigits <= 20, "round() digits must be between -20 and 20")
    return round(x, ndigits)


def _pow(a, b):
    return _power(a, b)


FUNCTIONS = {
    "abs": Func(abs),
    "round": Func(_round, 1, 2),
    "min": Func(min, 1, 20),
    "max": Func(max, 1, 20),
    "int": Func(lambda x: int(x)),
    "float": Func(float),
    "pow": Func(_pow, 2),
    "sqrt": Func(math.sqrt),
    "ceil": Func(math.ceil),
    "floor": Func(math.floor),
    "log": Func(math.log, 1, 2),
    "log2": Func(math.log2),
    "log10": Func(math.log10),
    "exp": Func(math.exp),
    "sin": Func(math.sin), "cos": Func(math.cos), "tan": Func(math.tan),
    "asin": Func(math.asin), "acos": Func(math.acos), "atan": Func(math.atan),
    "atan2": Func(math.atan2, 2),
    "sinh": Func(math.sinh), "cosh": Func(math.cosh), "tanh": Func(math.tanh),
    "degrees": Func(math.degrees), "radians": Func(math.radians),
    "hypot": Func(math.hypot, 1, 10),
    "factorial": Func(_factorial),
}

CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

_ALLOWED_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_ .,+-*/%()^\t")


def _check_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MathError("expression must produce a number")
    if isinstance(value, int):
        _require(value.bit_length() <= MAX_INT_BITS, "number too large")
    elif math.isfinite(value):
        _require(abs(value) <= MAX_FLOAT, "number too large")
    return value


def _power(a, b):
    if isinstance(a, int) and isinstance(b, int) and b >= 0:
        # Estimate the result size before computing it.
        if abs(a) > 1:
            _require(b * max(abs(a).bit_length(), 1) <= MAX_INT_BITS, "number too large")
        return a ** b
    _require(abs(b) <= 1e6 or abs(a) in (0, 1), "exponent too large")
    try:
        result = float(a) ** float(b)
    except OverflowError:
        raise MathError("number too large")
    except ZeroDivisionError:
        raise MathError("division by zero")
    if isinstance(result, complex):
        raise MathError("result is not a real number")
    return result


def _mult(a, b):
    if isinstance(a, int) and isinstance(b, int):
        _require(a.bit_length() + b.bit_length() <= MAX_INT_BITS + 1, "number too large")
    return a * b


def _div(a, b):
    _require(b != 0, "division by zero")
    return a / b


def _floordiv(a, b):
    _require(b != 0, "division by zero")
    return a // b


def _mod(a, b):
    _require(b != 0, "division by zero")
    return a % b


_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: _mult,
    ast.Div: _div,
    ast.FloorDiv: _floordiv,
    ast.Mod: _mod,
    ast.Pow: _power,
}


class _Evaluator:
    def __init__(self, functions, constants):
        self.functions = functions
        self.constants = constants
        self.steps = 0

    def eval(self, node):
        self.steps += 1
        _require(self.steps <= MAX_STEPS, "expression too complex")
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise MathError("only numbers are allowed")
            return _check_number(v)
        if isinstance(node, ast.Name):
            if node.id in self.constants:
                return self.constants[node.id]
            raise MathError(f"unknown name: {node.id}")
        if isinstance(node, ast.BinOp):
            op = _BINOPS.get(type(node.op))
            _require(op is not None, "operator not allowed")
            left = self.eval(node.left)
            right = self.eval(node.right)
            try:
                return _check_number(op(left, right))
            except OverflowError:
                raise MathError("number too large")
        if isinstance(node, ast.UnaryOp):
            operand = self.eval(node.operand)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            raise MathError("operator not allowed")
        if isinstance(node, ast.Call):
            _require(isinstance(node.func, ast.Name), "only named functions can be called")
            _require(not node.keywords, "keyword arguments are not allowed")
            func = self.functions.get(node.func.id)
            _require(func is not None, f"unknown function: {node.func.id}")
            for arg in node.args:
                _require(not isinstance(arg, ast.Starred), "starred arguments are not allowed")
            args = [self.eval(a) for a in node.args]
            try:
                return _check_number(func(args))
            except MathError:
                raise
            except OverflowError:
                raise MathError("number too large")
            except (ValueError, TypeError, ZeroDivisionError) as exc:
                raise MathError(f"{node.func.id}: {exc}") from None
        raise MathError(f"{type(node).__name__} is not allowed")


def _precheck(expr):
    _require(expr, "empty expression")
    _require(len(expr) <= MAX_LENGTH, f"expression too long (max {MAX_LENGTH} characters)")
    _require(all(c in _ALLOWED_CHARS for c in expr), "expression contains characters that are not allowed")
    _require("__" not in expr, "expression contains characters that are not allowed")
    depth = 0
    for c in expr:
        if c == "(":
            depth += 1
            _require(depth <= MAX_NESTING, "expression nested too deeply")
        elif c == ")":
            depth -= 1


def _validate_tree(tree):
    count = 0
    for node in ast.walk(tree):
        count += 1
        _require(count <= MAX_NODES, "expression too complex")
        if not isinstance(node, (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp,
                                 ast.UnaryOp, ast.Call, ast.operator, ast.unaryop)):
            raise MathError(f"{type(node).__name__} is not allowed")


def evaluate(expression, functions=None, constants=None):
    """Evaluate `expression`; return an int or float. Raises MathError.

    `functions` / `constants` extend (and may override) the defaults; function
    values must be Func instances so their argument counts are enforced.
    """
    expr = str(expression).strip().replace("^", "**")
    _precheck(expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise MathError("syntax error") from None
    except (RecursionError, MemoryError, ValueError):
        raise MathError("expression too complex") from None
    _validate_tree(tree)
    funcs = dict(FUNCTIONS)
    funcs.update(functions or {})
    consts = dict(CONSTANTS)
    consts.update(constants or {})
    for name, fn in funcs.items():
        if not isinstance(fn, Func):
            raise TypeError(f"safe_math function {name!r} must be a Func")
    return _Evaluator(funcs, consts).eval(tree.body)


def try_evaluate(expression, functions=None, constants=None):
    """Return (value, None) or (None, error message) — never raises MathError."""
    try:
        return evaluate(expression, functions, constants), None
    except MathError as exc:
        return None, str(exc)
