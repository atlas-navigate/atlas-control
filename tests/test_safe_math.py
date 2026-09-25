"""Adversarial tests for the shared calculator (safe_math) and both callers."""
import time
import unittest

import safe_math


ESCAPES = [
    "().__class__.__base__.__subclasses__()",
    "(1).__class__",
    "__import__('os').system('id')",
    "open('/etc/passwd')",
    "exec('1')",
    "eval('1')",
    "globals()",
    "lambda: 1",
    "[x for x in range(10)]",
    "(x for x in (1,2))",
    "{1: 2}",
    "'a' * 3",
    "b'x'",
    "f'{1}'",
    "abs.__call__(1)",
    "sqrt(x=4)",
    "max(*[1, 2])",
    "(lambda: 1)()",
    "1 if 1 else 2",
    "1 < 2",
    "a := 1",
    "x[0]",
    "getattr(1, 'real')",
    "type(1)",
    "int.__subclasses__()",
    "sqrt",
    "unknown_function(1)",
    "True + 1",
    "1j * 2",
    "1 << 100000",
    "~1",
    "not 1",
    "sqrt(4); import os",
    "１＋１",
]

DOS = [
    "9**9**9**9",
    "10**100000",
    "2**4097",
    "pow(10, 10**6)",
    "factorial(10**6)",
    "factorial(171)",
    "factorial(-1)",
    "(10**1000)*(10**1000)*(10**1000)*(10**1000)*(10**1000)",
    "(" * 40 + "1" + ")" * 40,
    "+".join(["1"] * 250),
    "1" * 400,
    "round(1.5, 10**9)",
    "exp(1000000)",
]


class SafeMathTests(unittest.TestCase):
    def test_ordinary_math(self):
        cases = {
            "2 + 3 * 4": 14,
            "2^10": 1024,
            "(1 + 2) * 3 / 2": 4.5,
            "7 // 2": 3,
            "7 % 4": 3,
            "-3 ** 2": -9,
            "sqrt(16)": 4.0,
            "round(pi, 3)": 3.142,
            "max(1, 5, 3)": 5,
            "hypot(3, 4)": 5.0,
            "factorial(10)": 3628800,
            "log(100, 10)": 2.0,
            "atan2(1, 1) * 4": 3.141592653589793,
            "1e3 + 1_000": 2000.0,
            "2**4000 > 0 or 1": None,
        }
        for expr, want in cases.items():
            with self.subTest(expr=expr):
                if want is None:
                    with self.assertRaises(safe_math.MathError):
                        safe_math.evaluate(expr)
                else:
                    self.assertAlmostEqual(safe_math.evaluate(expr), want)

    def test_escapes_are_rejected(self):
        for expr in ESCAPES:
            with self.subTest(expr=expr):
                value, err = safe_math.try_evaluate(expr)
                self.assertIsNone(value)
                self.assertTrue(err)

    def test_dos_expressions_fail_fast(self):
        for expr in DOS:
            with self.subTest(expr=expr[:40]):
                t0 = time.monotonic()
                value, err = safe_math.try_evaluate(expr)
                self.assertIsNone(value)
                self.assertTrue(err)
                self.assertLess(time.monotonic() - t0, 0.5)

    def test_division_by_zero_is_an_error_not_an_exception(self):
        for expr in ("1/0", "1//0", "1%0", "0**-1"):
            with self.subTest(expr=expr):
                self.assertEqual(safe_math.try_evaluate(expr)[0], None)

    def test_extra_functions_must_be_declared(self):
        with self.assertRaises(TypeError):
            safe_math.evaluate("f(1)", {"f": lambda x: x})
        self.assertEqual(safe_math.evaluate("f(2)", {"f": safe_math.Func(lambda x: x * 2)}), 4)
        with self.assertRaises(safe_math.MathError):
            safe_math.evaluate("f(1, 2)", {"f": safe_math.Func(lambda x: x)})


class CallerTests(unittest.TestCase):
    def test_app_calculator_uses_safe_math(self):
        import app
        self.assertEqual(app.safe_calc("2+2"), (4, None))
        value, err = app.safe_calc("().__class__")
        self.assertIsNone(value)
        value, err = app.safe_calc("9**9**9**9")
        self.assertIsNone(value)
        client = app.app.test_client()
        r = client.post("/api/calculator", json={"expression": "6*7"})
        self.assertEqual(r.get_json()["result"], 42)

    def test_ray_calc_tags_cannot_escape(self):
        import ai_manager
        mgr = ai_manager.AIManager.__new__(ai_manager.AIManager)
        text = ("[CALC: ().__class__.__base__.__subclasses__()] and "
                "[CALC: 9**9**9**9] but [CALC: 2*21] and [CALC: km_to_miles(10)]")
        out = mgr._process_calc_tags(text)
        self.assertIn("**42**", out)
        self.assertIn("**6.2137**", out)
        self.assertEqual(out.count("CALC ERROR"), 2)

    def test_ray_ballistic_drop_is_range_checked(self):
        import ai_manager
        mgr = ai_manager.AIManager.__new__(ai_manager.AIManager)
        val, err = mgr._eval_expr("ballistic_drop(300)")
        self.assertIsNone(err)
        t0 = time.monotonic()
        val, err = mgr._eval_expr("ballistic_drop(10**9)")
        self.assertIsNone(val)
        self.assertLess(time.monotonic() - t0, 0.5)
