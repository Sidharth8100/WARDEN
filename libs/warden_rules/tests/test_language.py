"""Independent precedence/semantics fixtures and explicit failure contracts."""

import asyncio
from decimal import Decimal

import pytest
from warden_rules.evaluator import evaluate
from warden_rules.language import RuleError, compile_rule, lex, parse, pretty


class FixedHistory:
    async def count(self, seconds: int) -> int:
        assert seconds == 86400
        return 2


@pytest.mark.parametrize("a", range(6))
@pytest.mark.parametrize("b", range(4))
@pytest.mark.parametrize("c", range(6))
async def test_144_arithmetic_precedence_conformance_cases(a: int, b: int, c: int) -> None:
    source = f'when {a} + {b} * {c} == {a + b * c} then tag("ok")'
    checked = compile_rule(source)
    assert await evaluate(checked, {}, FixedHistory()) is True
    assert parse(pretty(checked.program)).normalized() == checked.program.normalized()


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("10 - 3 - 2 == 5", True),
        ("10 / 2 / 5 == 1", True),
        ("(2 + 3) * 4 == 20", True),
        ("true or false and false", True),
        ("false and true or true", True),
        ("not false and true", True),
        ("-2 * -3 == 6", True),
        ("0.1 + 0.2 == 0.3", True),
        ("false and 1 / 0 > 1", False),
        ("true or 1 / 0 > 1", True),
        ('contains("new job", "job")', True),
        ('contains("Job", "job")', False),
        ('matches("price 123", "[0-9]+")', True),
        ('matches("abc", "^b")', False),
        ("count(changes, 24h) < 3", True),
        ("exists(price.current)", False),
        ("price.current != 0", False),
        ("not (price.current > 0)", True),
        ("price.current + 1 > 0", False),
        ("event.meaningful == true", True),
    ],
)
async def test_semantics(expression: str, expected: bool) -> None:
    checked = compile_rule(f'when {expression} then tag("ok")')
    assert await evaluate(checked, {"event.meaningful": True}, FixedHistory()) is expected


@pytest.mark.parametrize(
    "source",
    [
        "",
        "when",
        "when true then",
        'when true then tag("x") trailing',
        'when 1 < 2 < 3 then tag("x")',
        'when true then tag("x") +',
        'when true then tag("x") + tag("x")',
        'when true then tag("x") cooldown 0h',
        'when true then tag("x") cooldown 8d',
        'when true then tag("x") cooldown 1.5h',
        'when true then tag("x") cooldown 1 h',
        'when "bad\\q" then tag("x")',
        'when "unterminated then tag("x")',
        'when true then run("x")',
        'when true then tag("\\ud800")',
        "when true then tag(3)",
        'when event..score > 0 then tag("x")',
        'when !true then tag("x")',
        'when 1234567890123456789 > 0 then tag("x")',
        'when 0.1234567 > 0 then tag("x")',
    ],
)
def test_invalid_syntax_is_a_structured_diagnostic(source: str) -> None:
    with pytest.raises(RuleError) as caught:
        parse(source)
    assert caught.value.span.start >= 0
    assert caught.value.as_dict()["code"]


@pytest.mark.parametrize(
    "expression",
    [
        'price.current < "cheap"',
        "1 and true",
        'contains(1, "x")',
        'matches("x", text.after)',
        'matches("x", "(a)\\\\1")',
        'matches("x", "(?=x)")',
        "count(price.current, 24h) > 1",
        "count(changes, 24) > 1",
        "unknown(1)",
        "exists(1)",
        "true + false == 1",
        'false and price.current < "x"',
    ],
)
def test_static_type_errors_even_in_unreachable_branches(expression: str) -> None:
    with pytest.raises(RuleError):
        compile_rule(f'when {expression} then tag("ok")')


def test_spans_suggestions_utf8_and_independent_ast() -> None:
    source = '# emoji 😀\nwhen price.curent < 1 then tag("x")'
    with pytest.raises(RuleError) as caught:
        compile_rule(source)
    error = caught.value
    assert error.code == "unknown_field" and "price.current" in error.suggestions
    assert error.span.line == 2 and source[error.span.start : error.span.end] == "price.curent"
    tree = parse('when 2 + 3 * 4 == 14 then tag("x")').condition
    assert tree.value == "==" and tree.children[0].value == "+"
    assert tree.children[0].children[1].value == "*"
    assert lex('when true then tag("x")')[-1].kind == "EOF"


async def test_steps_deadline_decimal_and_context_limits() -> None:
    checked = compile_rule('when price.current + 1 > 0 then tag("x")')
    with pytest.raises(RuleError, match="budget"):
        await evaluate(checked, {"price.current": Decimal(2)}, FixedHistory(), steps=1)
    with pytest.raises(RuleError, match="arithmetic"):
        await evaluate(compile_rule('when 1 / 0 > 0 then tag("x")'), {}, FixedHistory())
    with pytest.raises(ValueError):
        await evaluate(checked, {"price.current": Decimal("NaN")}, FixedHistory())
    with pytest.raises(ValueError):
        await evaluate(checked, {"text.after": "x" * 65537}, FixedHistory())

    class SlowHistory:
        async def count(self, seconds: int) -> int:
            await asyncio.sleep(1)
            return 0

    with pytest.raises(RuleError, match="deadline"):
        await evaluate(
            compile_rule('when count(changes, 24h) < 3 then tag("x")'),
            {},
            SlowHistory(),
            timeout_s=0.01,
        )


def test_parser_depth_and_size_limits_are_not_python_recursion_errors() -> None:
    for expression in [
        "(" * 1000 + "true" + ")" * 1000,
        " + ".join(["1"] * 1000) + " > 0",
        "true " * 5000,
    ]:
        with pytest.raises(RuleError):
            parse(f'when {expression} then tag("x")')
