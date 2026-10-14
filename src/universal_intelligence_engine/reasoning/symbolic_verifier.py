"""Deterministic, bounded arithmetic and dimensional-consistency checks.

This module verifies only explicitly supplied expressions; it is not a domain
expert system and never certifies clinical, weapons, or operational decisions.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    code: str
    message: str
    computed_value: float | None = None


_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_number(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        value = float(node.value)
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _safe_number(node.left), _safe_number(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1e100):
            raise ValueError("exponent or base exceeds verification bounds")
        value = _BINARY[type(node.op)](left, right)
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        value = _UNARY[type(node.op)](_safe_number(node.operand))
    else:
        raise ValueError("only numeric literals and basic arithmetic are allowed")
    if not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError("non-finite or out-of-bounds arithmetic result")
    return value


def evaluate_arithmetic(expression: str) -> float:
    if not isinstance(expression, str) or len(expression) > 256:
        raise ValueError("expression must be a string of at most 256 characters")
    tree = ast.parse(expression, mode="eval")
    return _safe_number(tree.body)


def verify_arithmetic(expression: str, expected: float, *, tolerance: float = 1e-9) -> VerificationResult:
    try:
        actual = evaluate_arithmetic(expression)
        expected_value = float(expected)
        if not math.isfinite(expected_value) or not math.isfinite(tolerance) or tolerance < 0:
            return VerificationResult(False, "invalid_bounds", "expected value/tolerance is invalid")
        valid = math.isclose(actual, expected_value, rel_tol=tolerance, abs_tol=tolerance)
        return VerificationResult(valid, "verified" if valid else "arithmetic_mismatch",
                                  "arithmetic matches expected value" if valid else "computed value differs from assertion", actual)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return VerificationResult(False, "invalid_expression", str(exc))


# Unit factor to SI and physical dimension. Deliberately small and explicit.
_UNITS = {
    "m": (1.0, "length"), "km": (1000.0, "length"), "cm": (0.01, "length"),
    "mm": (0.001, "length"), "s": (1.0, "time"), "sec": (1.0, "time"),
    "min": (60.0, "time"), "h": (3600.0, "time"), "kg": (1.0, "mass"),
    "g": (0.001, "mass"), "mg": (1e-6, "mass"), "L": (1.0, "volume"),
    "mL": (0.001, "volume"), "K": (1.0, "temperature"),
}


def verify_unit_conversion(value: float, source_unit: str, target_unit: str, expected: float,
                           *, tolerance: float = 1e-9) -> VerificationResult:
    if source_unit not in _UNITS or target_unit not in _UNITS:
        return VerificationResult(False, "unknown_unit", "unit is not in the explicit verification registry")
    source_factor, source_dimension = _UNITS[source_unit]
    target_factor, target_dimension = _UNITS[target_unit]
    if source_dimension != target_dimension:
        return VerificationResult(False, "dimension_mismatch", "source and target units have different dimensions")
    try:
        actual = float(value) * source_factor / target_factor
        expected = float(expected)
        if not all(map(math.isfinite, (actual, expected, tolerance))) or tolerance < 0:
            raise ValueError("values or tolerance are outside verification bounds")
    except (TypeError, ValueError, OverflowError):
        return VerificationResult(False, "invalid_quantity", "quantity must be finite numeric values")
    valid = math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)
    return VerificationResult(valid, "verified" if valid else "conversion_mismatch",
                              "unit conversion matches" if valid else "converted value differs from assertion", actual)


_ARITHMETIC_ASSERTION = re.compile(r"(?<![\w.])([0-9][0-9 .()+*/\-]{0,80})\s*(?:=|equals)\s*(-?[0-9]+(?:\.[0-9]+)?)", re.I)


def verify_numeric_claims(text: str) -> list[dict]:
    """Check simple inline arithmetic equalities; never infer truth from prose alone."""
    findings = []
    for match in _ARITHMETIC_ASSERTION.finditer(text or ""):
        expression = match.group(1).strip()
        try:
            # Avoid accidentally treating whitespace-separated prose as an expression.
            if not re.fullmatch(r"[0-9 .()+*/\-]+", expression) or not any(op in expression for op in "+*/"):
                continue
            result = verify_arithmetic(expression, float(match.group(2)))
            if not result.valid:
                findings.append({"start": match.start(), "expression": expression,
                                 "asserted": float(match.group(2)), "code": result.code,
                                 "message": result.message, "computed": result.computed_value})
        except (ValueError, SyntaxError):
            continue
    return findings
