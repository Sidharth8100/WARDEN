import pytest
from hypothesis import given
from hypothesis import strategies as st

from warden_core.mathutil import clamp

finite = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False)


@given(x=finite, a=finite, b=finite)
def test_result_is_within_bounds(x, a, b):
    lo, hi = min(a, b), max(a, b)
    assert lo <= clamp(x, lo, hi) <= hi


@given(x=finite, a=finite, b=finite)
def test_value_already_inside_is_unchanged(x, a, b):
    lo, hi = min(a, b), max(a, b)
    if lo <= x <= hi:
        assert clamp(x, lo, hi) == x


@given(x=finite, a=finite, b=finite)
def test_idempotent(x, a, b):
    lo, hi = min(a, b), max(a, b)
    once = clamp(x, lo, hi)
    assert clamp(once, lo, hi) == once


def test_inverted_bounds_rejected():
    with pytest.raises(ValueError):
        clamp(1.0, 5.0, 2.0)
