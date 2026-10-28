"""Bounded v1 lexer, Pratt parser, canonical printer and static checker."""

import hashlib
import importlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, cast


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    line: int
    column: int


class RuleError(ValueError):
    def __init__(self, code: str, message: str, span: Span, suggestions: tuple[str, ...] = ()):
        super().__init__(message)
        self.code, self.span, self.suggestions = code, span, suggestions

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "start": self.span.start,
            "end": self.span.end,
            "line": self.span.line,
            "column": self.span.column,
            "suggestions": list(self.suggestions),
        }


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    span: Span


def lex(source: str) -> list[Token]:
    try:
        size = len(source.encode("utf-8"))
    except UnicodeError as exc:
        raise RuleError("encoding", "Source must be valid UTF-8", Span(0, 0, 1, 1)) from exc
    if size > 16_384:
        raise RuleError("source_limit", "Source exceeds 16 KiB", Span(0, len(source), 1, 1))
    tokens: list[Token] = []
    i, nesting = 0, 0
    while i < len(source):
        start = i
        c = source[i]
        if c.isspace():
            i += 1
            continue
        if c == "#":
            while i < len(source) and source[i] != "\n":
                i += 1
            continue
        span = Span(
            start, start + 1, source.count("\n", 0, start) + 1, start - source.rfind("\n", 0, start)
        )
        kind = c
        if c == '"':
            i += 1
            escaped = False
            while i < len(source):
                current = source[i]
                i += 1
                if current == '"' and not escaped:
                    break
                escaped = current == "\\" and not escaped
            else:
                raise RuleError("string", "Unterminated string", span)
            try:
                decoded = json.loads(source[start:i])
                if not isinstance(decoded, str):
                    raise ValueError("not string")
                decoded.encode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise RuleError("string", "Invalid string escape or character", span) from exc
            kind = "STRING"
        elif c.isascii() and c.isdigit():
            while i < len(source) and source[i].isascii() and source[i].isdigit():
                i += 1
            integer_end = i
            if i + 1 < len(source) and source[i] == "." and source[i + 1].isdigit():
                i += 1
                while i < len(source) and source[i].isascii() and source[i].isdigit():
                    i += 1
            number = source[start:i]
            if sum(x.isdigit() for x in number) > 18 or (
                "." in number and len(number.partition(".")[2]) > 6
            ):
                raise RuleError("number_limit", "Decimal literal exceeds precision limit", span)
            kind = "NUMBER"
            if i < len(source) and source[i] in "smhd":
                if i != integer_end or int(number) <= 0:
                    raise RuleError("duration", "Duration requires a positive integer", span)
                i += 1
                kind = "DURATION"
        elif c.isascii() and (c.isalpha() or c == "_"):
            i += 1
            while (
                i < len(source)
                and source[i].isascii()
                and (source[i].isalnum() or source[i] == "_")
            ):
                i += 1
            kind = "IDENT"
        elif source[i : i + 2] in ("==", "!=", "<=", ">="):
            i += 2
            kind = source[start:i]
        elif c in "()+-*/<>,.":
            i += 1
            nesting += 1 if c == "(" else -1 if c == ")" else 0
            if nesting > 64:
                raise RuleError("depth_limit", "Nesting exceeds 64", span)
        else:
            raise RuleError("character", "Unexpected character", span)
        tokens.append(Token(kind, source[start:i], Span(start, i, span.line, span.column)))
        if len(tokens) > 4096:
            raise RuleError("token_limit", "Token budget exceeded", span)
    tokens.append(Token("EOF", "", Span(i, i, source.count("\n") + 1, i - source.rfind("\n"))))
    return tokens


@dataclass(frozen=True)
class Node:
    kind: str
    value: str
    children: tuple["Node", ...]
    span: Span
    depth: int = 1

    def normalized(self) -> tuple[object, ...]:
        value = str(Decimal(self.value).normalize()) if self.kind == "NUMBER" else self.value
        return self.kind, value, tuple(x.normalized() for x in self.children)


@dataclass(frozen=True)
class Action:
    kind: str
    target: str
    span: Span


@dataclass(frozen=True)
class Program:
    condition: Node
    actions: tuple[Action, ...]
    cooldown_s: int

    def normalized(self) -> tuple[object, ...]:
        return (
            self.condition.normalized(),
            tuple((x.kind, x.target) for x in self.actions),
            self.cooldown_s,
        )


POWERS = {
    "or": 10,
    "and": 20,
    "==": 30,
    "!=": 30,
    "<": 30,
    "<=": 30,
    ">": 30,
    ">=": 30,
    "+": 40,
    "-": 40,
    "*": 50,
    "/": 50,
}


def duration(token: Token) -> int:
    if token.kind != "DURATION":
        raise RuleError("duration", "Expected a duration such as 24h", token.span)
    seconds = int(token.text[:-1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[token.text[-1]]
    if not 1 <= seconds <= 604_800:
        raise RuleError(
            "duration", "Duration must be between one second and seven days", token.span
        )
    return seconds


class Parser:
    def __init__(self, source: str):
        self.tokens, self.index, self.nodes = lex(source), 0, 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self) -> Token:
        token = self.current
        if token.kind != "EOF":
            self.index += 1
        return token

    def expect(self, text: str) -> Token:
        if self.current.text != text:
            raise RuleError("syntax", f"Expected {text!r}", self.current.span)
        return self.take()

    def node(self, kind: str, value: str, children: tuple[Node, ...], span: Span) -> Node:
        self.nodes += 1
        depth = 1 + max((x.depth for x in children), default=0)
        if self.nodes > 2048 or depth > 64:
            raise RuleError("ast_limit", "Expression size/depth limit exceeded", span)
        return Node(kind, value, children, span, depth)

    def expression(self, minimum: int = 0, recursion: int = 0) -> Node:
        if recursion > 192:
            raise RuleError("depth_limit", "Parser recursion budget exceeded", self.current.span)
        token = self.take()
        if token.text in ("not", "-"):
            left = self.node("unary", token.text, (self.expression(60, recursion + 1),), token.span)
        elif token.text == "(":
            left = self.expression(0, recursion + 1)
            self.expect(")")
        elif token.kind in ("STRING", "NUMBER", "DURATION"):
            value = str(json.loads(token.text)) if token.kind == "STRING" else token.text
            if token.kind == "DURATION":
                value = str(duration(token))
            left = self.node(token.kind, value, (), token.span)
        elif token.text in ("true", "false"):
            left = self.node("BOOL", token.text, (), token.span)
        elif token.kind == "IDENT" and token.text not in ("then", "and", "or", "cooldown"):
            name = token.text
            while self.current.text == ".":
                self.take()
                if self.current.kind != "IDENT":
                    raise RuleError("field", "Expected field name after dot", self.current.span)
                name += "." + self.take().text
            if self.current.text == "(":
                self.take()
                children = []
                if self.current.text != ")":
                    children.append(self.expression(0, recursion + 1))
                    while self.current.text == ",":
                        self.take()
                        children.append(self.expression(0, recursion + 1))
                        if len(children) > 3:
                            raise RuleError("arity", "Too many arguments", token.span)
                self.expect(")")
                left = self.node("call", name, tuple(children), token.span)
            else:
                field_span = Span(
                    token.span.start,
                    self.tokens[self.index - 1].span.end,
                    token.span.line,
                    token.span.column,
                )
                left = self.node("field", name, (), field_span)
        else:
            raise RuleError("expression", "Expected an expression", token.span)
        compared = False
        while self.current.text in POWERS and POWERS[self.current.text] >= minimum:
            operator = self.take()
            power = POWERS[operator.text]
            if power == 30 and compared:
                raise RuleError("comparison", "Join comparisons with and", operator.span)
            right = self.expression(power + 1, recursion + 1)
            left = self.node("binary", operator.text, (left, right), operator.span)
            compared = power == 30
        return left

    def parse(self) -> Program:
        self.expect("when")
        condition = self.expression()
        self.expect("then")
        actions: list[Action] = []
        while True:
            token = self.take()
            if token.text not in ("webhook", "email", "tag"):
                raise RuleError("action", "Expected webhook, email or tag", token.span)
            self.expect("(")
            target = self.take()
            if target.kind != "STRING":
                raise RuleError("action", "Action target must be a string literal", target.span)
            name = cast(str, json.loads(target.text))
            if not 1 <= len(name) <= (50 if token.text == "tag" else 128) or any(
                ord(c) < 32 for c in name
            ):
                raise RuleError("action", "Invalid action target", target.span)
            if (token.text, name) in {(x.kind, x.target) for x in actions}:
                raise RuleError("action", "Duplicate action", target.span)
            self.expect(")")
            actions.append(Action(token.text, name, token.span))
            if len(actions) > 8:
                raise RuleError("action_limit", "At most eight actions are allowed", token.span)
            if self.current.text != "+":
                break
            self.take()
        cooldown = 0
        if self.current.text == "cooldown":
            self.take()
            cooldown = duration(self.take())
        if self.current.kind != "EOF":
            raise RuleError("trailing", "Unexpected trailing input", self.current.span)
        return Program(condition, tuple(actions), cooldown)


def parse(source: str) -> Program:
    return Parser(source).parse()


def pretty(program: Program) -> str:
    def expression(node: Node) -> str:
        if node.kind == "STRING":
            return json.dumps(node.value, ensure_ascii=True)
        if node.kind == "DURATION":
            return node.value + "s"
        if node.kind == "NUMBER":
            return format(Decimal(node.value).normalize(), "f")
        if node.kind == "unary":
            return f"({node.value} {expression(node.children[0])})"
        if node.kind == "binary":
            return f"({expression(node.children[0])} {node.value} {expression(node.children[1])})"
        if node.kind == "call":
            return node.value + "(" + ", ".join(expression(x) for x in node.children) + ")"
        return node.value

    actions = " + ".join(x.kind + "(" + json.dumps(x.target) + ")" for x in program.actions)
    suffix = f" cooldown {program.cooldown_s}s" if program.cooldown_s else ""
    return f"when {expression(program.condition)} then {actions}{suffix}"


FIELDS: dict[str, str] = {
    "event.kind": "String",
    "event.score": "Decimal",
    "event.meaningful": "Bool",
    "text.before": "String",
    "text.after": "String",
    "price.previous": "Decimal",
    "price.current": "Decimal",
    "price.drop_percent": "Decimal",
    "price.currency": "String",
}


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        row = [i]
        for j, b in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = row
    return previous[-1]


class RegexPattern(Protocol):
    def search(self, text: str) -> object | None: ...


class RegexOptions(Protocol):
    log_errors: bool
    max_mem: int


class RegexBackend(Protocol):
    def Options(self) -> RegexOptions: ...
    def compile(self, pattern: str, options: RegexOptions) -> RegexPattern: ...


@dataclass(frozen=True)
class CheckedProgram:
    program: Program
    patterns: dict[str, RegexPattern]
    digest: str


def check(program: Program) -> CheckedProgram:
    patterns: dict[str, RegexPattern] = {}

    def infer(node: Node) -> str:
        if node.kind in ("NUMBER", "STRING", "BOOL", "DURATION"):
            return {
                "NUMBER": "Decimal",
                "STRING": "String",
                "BOOL": "Bool",
                "DURATION": "Duration",
            }[node.kind]
        if node.kind == "field":
            if node.value not in FIELDS:
                candidates = sorted((edit_distance(node.value, name), name) for name in FIELDS)
                suggestions = tuple(name for distance, name in candidates if distance <= 2)[:3]
                raise RuleError(
                    "unknown_field", "Unknown field " + node.value, node.span, suggestions
                )
            return FIELDS[node.value]
        if node.kind == "call" and node.value == "count":
            if (
                len(node.children) == 2
                and node.children[0].kind == "field"
                and (node.children[0].value == "changes" and node.children[1].kind == "DURATION")
            ):
                return "Decimal"
            raise RuleError("type", "Use count(changes, duration)", node.span)
        types = [infer(x) for x in node.children]
        if node.kind == "unary":
            expected = "Bool" if node.value == "not" else "Decimal"
            if types == [expected]:
                return expected
        elif node.kind == "binary":
            if node.value in ("and", "or") and types == ["Bool", "Bool"]:
                return "Bool"
            if node.value in ("+", "-", "*", "/") and types == ["Decimal", "Decimal"]:
                return "Decimal"
            if node.value in ("==", "!=") and types[0] == types[1] and types[0] != "Duration":
                return "Bool"
            if node.value in ("<", "<=", ">", ">=") and types == ["Decimal", "Decimal"]:
                return "Bool"
        elif node.kind == "call":
            if node.value == "exists" and len(types) == 1 and node.children[0].kind == "field":
                return "Bool"
            if node.value in ("contains", "matches") and types == ["String", "String"]:
                if node.value == "matches":
                    pattern = node.children[1]
                    if pattern.kind != "STRING" or len(pattern.value.encode()) > 512:
                        raise RuleError(
                            "regex", "Regex must be a literal of at most 512 bytes", pattern.span
                        )
                    backend = cast(RegexBackend, importlib.import_module("re2"))
                    options = backend.Options()
                    options.log_errors, options.max_mem = False, 1_048_576
                    try:
                        patterns[pattern.value] = backend.compile(pattern.value, options)
                    except Exception as exc:
                        raise RuleError(
                            "regex", "Invalid or unsupported RE2 pattern", pattern.span
                        ) from exc
                return "Bool"
        raise RuleError("type", "Invalid operand types or function signature", node.span)

    if infer(program.condition) != "Bool":
        raise RuleError("type", "Rule condition must be Bool", program.condition.span)
    return CheckedProgram(program, patterns, hashlib.sha256(pretty(program).encode()).hexdigest())


def compile_rule(source: str) -> CheckedProgram:
    return check(parse(source))
