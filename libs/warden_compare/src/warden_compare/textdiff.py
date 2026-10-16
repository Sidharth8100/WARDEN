"""Myers' O(ND) diff algorithm stub and edit script application."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Keep:
    """Retain `n` items from the source sequence without modification."""

    n: int


@dataclass(frozen=True)
class Delete:
    """Delete `n` items from the source sequence."""

    n: int


@dataclass(frozen=True)
class Insert:
    """Insert `items` into the target sequence."""

    items: list[str]


Op = Keep | Delete | Insert
EditScript = list[Op]


def compute_edit_script(source: list[str], target: list[str]) -> EditScript:
    """[YOU IMPLEMENT] Compute the SHORTEST edit script transforming
    `source` into `target` using Myers' O(ND) diff algorithm, where N
    is len(source)+len(target) and D is the size of the actual edit
    distance. Return an EditScript: an ordered list of Op values, each
    either Keep(n), Delete(n), or Insert(items). Applying the ops in
    order to `source` must reconstruct `target` exactly.

    This is the real algorithm behind `diff` and `git diff`. Do not
    use difflib, Levenshtein libraries, or any other diff library —
    that defeats the point.

    Hint L1: think in terms of a grid where one axis is consumed source
    tokens and the other is consumed target tokens. A "move right" is
    a delete, a "move down" is an insert, a "move diagonally" is a
    keep (only possible where source[i] == target[j]). You are looking
    for the shortest path from (0,0) to (len(source), len(target))
    that uses the fewest non-diagonal moves.
    Hint L2: Myers' algorithm walks "diagonals" k = i - j, and for each
    increasing edit distance d = 0, 1, 2, ..., tracks the FURTHEST
    reachable (i, j) on each diagonal k reachable using exactly d
    non-diagonal moves, extending through any available diagonal
    (equal-element) run for free after each move. Keep a per-d array
    (dict or list, offset-indexed since k can be negative) of the
    furthest x reached per diagonal; the answer is the smallest d for
    which some diagonal reaches the bottom-right corner. You will need
    to also keep enough history (e.g. store the full per-d arrays) to
    WALK BACK afterward and reconstruct the actual sequence of moves,
    not just the distance.
    Hint L3: this is a well-documented classical algorithm — the
    original paper is "An O(ND) Difference Algorithm and Its
    Variations" by Eugene Myers (1986). Multiple free, readable
    walkthroughs with pseudocode exist; find one, understand the
    "greedy LCS/SES" formulation, and implement it from the
    pseudocode in your own code, rather than copying any found source
    verbatim. Tokenize your source/target as LINES for text diffing
    (split on '\\n'), not characters — this matters for performance
    and for matching how Day 6's tree diff will later use this same
    function at a different granularity.
    """
    raise NotImplementedError("see docstring")


def apply_edit_script(source: list[str], script: EditScript) -> list[str]:
    """Interpret `script`'s ops against `source`, producing the
    reconstructed target. Straightforward: walk the ops, for Keep
    copy n items forward, for Delete skip n items, for Insert splice
    in the given items. (AI-implemented — this is not the hard part.)
    """
    result: list[str] = []
    idx = 0
    for op in script:
        if isinstance(op, Keep):
            result.extend(source[idx : idx + op.n])
            idx += op.n
        elif isinstance(op, Delete):
            idx += op.n
        elif isinstance(op, Insert):
            result.extend(op.items)
        else:
            raise ValueError(f"Unrecognized EditScript operation: {op}")
    return result
