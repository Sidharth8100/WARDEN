"""Student test suite for Stub #1: Myers O(ND) diff algorithm.

STUDENT STUB TEST:
Tagged with @pytest.mark.student_todo. Excluded from default `make check`.
Run explicitly via `make check-todos`.
"""

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from warden_compare.textdiff import (
    Delete,
    EditScript,
    Insert,
    apply_edit_script,
    compute_edit_script,
)


def _dp_lcs_edit_distance(source: list[str], target: list[str]) -> int:
    """Independent Dynamic-Programming LCS reference oracle.

    Computes the theoretical minimum edit distance (number of deletions + insertions)
    to transform `source` into `target`.
    LCS recurrence:
      dp[i][j] = dp[i-1][j-1] + 1 if source[i-1] == target[j-1]
      dp[i][j] = max(dp[i-1][j], dp[i][j-1]) otherwise
    Edit distance D = len(source) + len(target) - 2 * LCS.
    """
    m, n = len(source), len(target)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if source[i - 1] == target[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_len = dp[m][n]
    return (m - lcs_len) + (n - lcs_len)


def _count_edit_operations(script: EditScript) -> int:
    """Count the total number of deleted and inserted tokens in an edit script."""
    total = 0
    for op in script:
        if isinstance(op, Delete):
            total += op.n
        elif isinstance(op, Insert):
            total += len(op.items)
    return total


@pytest.mark.student_todo
class TestMyersDiffStudent:
    """Comprehensive test matrix and property tests for Myers diff algorithm."""

    @pytest.mark.parametrize(
        ("source", "target", "expected_distance"),
        [
            # Identical sequences (0 edits)
            (["a", "b", "c"], ["a", "b", "c"], 0),
            # Completely disjoint sequences (len(s) deletes + len(t) inserts)
            (["a", "b"], ["c", "d"], 4),
            # Single-line change
            (["a", "b", "c"], ["a", "x", "c"], 2),
            # Insert-only
            (["a", "c"], ["a", "b", "c"], 1),
            # Delete-only
            (["a", "b", "c"], ["a", "c"], 1),
            # Empty source
            ([], ["x", "y"], 2),
            # Empty target
            (["x", "y"], [], 2),
            # Both empty
            ([], [], 0),
        ],
    )
    def test_table_driven_cases(self, source: list[str], target: list[str], expected_distance: int):
        script = compute_edit_script(source, target)

        # 1. Correctness: applying script to source must equal target
        reconstructed = apply_edit_script(source, script)
        assert reconstructed == target

        # 2. Optimality: script must achieve the theoretical minimum edit distance
        assert _count_edit_operations(script) == expected_distance

    @settings(max_examples=30, deadline=None)
    @given(
        source=st.lists(st.text(min_size=1, max_size=5), min_size=0, max_size=15),
        target=st.lists(st.text(min_size=1, max_size=5), min_size=0, max_size=15),
    )
    def test_hypothesis_correctness_and_optimality(self, source: list[str], target: list[str]):
        """Property test asserting BOTH correctness and minimal edit distance.

        WHY CHECKING BOTH PROPERTIES MATTERS:
        Checking correctness alone (apply(source, script) == target) is insufficient because a
        naive implementation that emits Delete(len(source)) followed by Insert(target) is 100%
        'correct' but completely fails to compute the Shortest Edit Script (SES). Checking
        optimality against the independent DP-LCS oracle ensures that Myers' O(ND) algorithm
        actually discovered the minimal edit path.
        """
        script = compute_edit_script(source, target)

        # Property 1: Correctness
        reconstructed = apply_edit_script(source, script)
        assert reconstructed == target

        # Property 2: Optimality
        oracle_distance = _dp_lcs_edit_distance(source, target)
        assert _count_edit_operations(script) == oracle_distance
