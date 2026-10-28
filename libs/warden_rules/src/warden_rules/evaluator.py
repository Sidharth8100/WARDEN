"""Step/deadline-bounded async tree walker. No network implementation inside."""

import asyncio
import time
from collections.abc import Mapping
from decimal import Decimal, DecimalException, localcontext
from typing import Protocol

from warden_rules.language import FIELDS, CheckedProgram, Node, RuleError

type Value = Decimal | str | bool | None


class History(Protocol):
    async def count(self, seconds: int) -> int: ...


async def evaluate(
    checked: CheckedProgram,
    context: Mapping[str, Value],
    history: History,
    *,
    steps: int = 10_000,
    timeout_s: float = 0.1,
) -> bool:
    if steps < 1 or not 0 < timeout_s <= 30:
        raise ValueError("positive bounded evaluation limits required")
    for name, value in context.items():
        expected = FIELDS.get(name)
        if expected is None:
            raise RuleError("context", "Undeclared context field", checked.program.condition.span)
        if value is not None and not (
            (
                expected == "Decimal"
                and isinstance(value, Decimal)
                and value.is_finite()
                and abs(value) <= Decimal("1e18")
            )
            or (expected == "Bool" and type(value) is bool)
            or (expected == "String" and isinstance(value, str) and len(value.encode()) <= 65_536)
        ):
            raise RuleError(
                "context", "Invalid or oversized context value", checked.program.condition.span
            )
    deadline, remaining = time.monotonic() + timeout_s, steps

    async def walk(node: Node) -> Value:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or time.monotonic() >= deadline:
            raise RuleError("evaluation_limit", "Evaluation budget exceeded", node.span)
        if node.kind in ("NUMBER", "DURATION"):
            return Decimal(node.value)
        if node.kind == "STRING":
            return node.value
        if node.kind == "BOOL":
            return node.value == "true"
        if node.kind == "field":
            return context.get(node.value)
        if node.kind == "call" and node.value == "count":
            count = await history.count(int(node.children[1].value))
            if not 0 <= count <= 1_000_000_000:
                raise ValueError("history returned invalid count")
            return Decimal(count)
        if node.kind == "call" and node.value == "exists":
            return context.get(node.children[0].value) is not None
        left = await walk(node.children[0])
        if node.kind == "unary":
            if node.value == "not":
                return not left
            return -left if isinstance(left, Decimal) else None
        if node.value == "and" and left is False:
            return False
        if node.value == "or" and left is True:
            return True
        right = await walk(node.children[1])
        if node.value in ("and", "or"):
            return right
        if left is None or right is None:
            return (
                False
                if node.value in ("==", "!=", "<", "<=", ">", ">=", "contains", "matches")
                else None
            )
        if node.kind == "call" and isinstance(left, str) and isinstance(right, str):
            return (
                right in left
                if node.value == "contains"
                else checked.patterns[right].search(left) is not None
            )
        if node.value == "==":
            return left == right
        if node.value == "!=":
            return left != right
        if isinstance(left, Decimal) and isinstance(right, Decimal):
            if node.value == "<":
                return left < right
            if node.value == "<=":
                return left <= right
            if node.value == ">":
                return left > right
            if node.value == ">=":
                return left >= right
            try:
                with localcontext() as numeric:
                    numeric.prec = 28
                    if node.value == "+":
                        result = left + right
                    elif node.value == "-":
                        result = left - right
                    elif node.value == "*":
                        result = left * right
                    else:
                        result = left / right
                if not result.is_finite() or abs(result) > Decimal("1e18"):
                    raise ValueError("magnitude")
                return result
            except (DecimalException, ValueError) as exc:
                raise RuleError(
                    "arithmetic", "Undefined or excessive arithmetic", node.span
                ) from exc
        raise RuleError("evaluation", "Invalid checked expression", node.span)

    try:
        async with asyncio.timeout(timeout_s):
            result = await walk(checked.program.condition)
    except TimeoutError as exc:
        raise RuleError(
            "evaluation_limit", "Evaluation deadline exceeded", checked.program.condition.span
        ) from exc
    if type(result) is not bool:
        raise RuleError(
            "evaluation", "Condition did not produce Bool", checked.program.condition.span
        )
    return result
