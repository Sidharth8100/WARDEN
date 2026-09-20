def clamp(value: float, lo: float, hi: float) -> float:
    """Return value limited to the closed interval [lo, hi]."""
    if lo > hi:
        raise ValueError(f"lo ({lo}) must not exceed hi ({hi})")
    return max(lo, min(value, hi))
