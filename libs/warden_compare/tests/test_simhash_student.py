"""Student test suite for Stub #2: SimHash near-duplicate fingerprinting.

STUDENT STUB TEST:
Tagged with @pytest.mark.student_todo. Excluded from default `make check`.
Run explicitly via `make check-todos`.
"""

import random

import pytest

from warden_compare.simhash import compute_simhash, hamming_distance

BASE_CORPUS = [
    "distributed systems require consensus algorithms to ensure fault tolerance in partitions",
    "the quick brown fox jumps over the lazy dog in the sunny afternoon near the river bank",
    "relational databases enforce acid properties through write ahead logging and mvcc",
    "modern web applications rely on responsive css layouts client side rendering and rest apis",
    "cryptographic protocols use public key infrastructure to authenticate peers securely",
]


def _shingles(text: str, k: int = 4) -> set[str]:
    """Independent oracle: extract word k-shingles without SimHash."""
    words = text.split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _jaccard_similarity(text_a: str, text_b: str, k: int = 4) -> float:
    """Independent brute-force Jaccard similarity oracle on shingle sets."""
    s_a = _shingles(text_a, k)
    s_b = _shingles(text_b, k)
    if not s_a or not s_b:
        return 1.0 if s_a == s_b else 0.0
    return len(s_a & s_b) / len(s_a | s_b)


@pytest.mark.student_todo
class TestSimHashStudent:
    """Statistical and property test matrix for SimHash student implementation."""

    def test_empty_string_returns_deterministic_value(self):
        """SimHash of empty text must return a well-defined integer, e.g. 0."""
        h1 = compute_simhash("")
        h2 = compute_simhash("")
        assert h1 == h2
        assert isinstance(h1, int)
        assert h1 == 0

    def test_identical_text_has_zero_hamming_distance(self):
        """Identical texts must produce identical fingerprints (Hamming distance = 0)."""
        text = "The quick brown fox jumps over the lazy dog"
        h1 = compute_simhash(text)
        h2 = compute_simhash(text)
        assert h1 == h2
        assert hamming_distance(h1, h2) == 0

    def test_statistical_distribution_across_200_trials(self):
        """Statistical test aggregating across 200+ trials.

        STATISTICAL PROPERTY TESTED:
        Because SimHash is probabilistic, no single pair can be asserted with an exact threshold.
        Across 200+ trials:
        1. Mean Hamming distance for near-duplicate pairs must be significantly lower
           than mean Hamming distance for unrelated pairs.
        2. High Jaccard similarity must strongly correspond to lower Hamming distance.
        """
        random.seed(42)
        near_distances: list[int] = []
        unrelated_distances: list[int] = []

        for _ in range(200):
            base_doc = random.choice(BASE_CORPUS)
            words = base_doc.split()

            # Create near-duplicate by mutating 1-2 words (high similarity)
            mutated_words = list(words)
            mut_idx = random.randint(0, len(words) - 1)
            mutated_words[mut_idx] = f"token_{random.randint(100, 999)}"
            near_doc = " ".join(mutated_words)

            # Unrelated document
            unrelated_doc = random.choice([d for d in BASE_CORPUS if d != base_doc])

            h_base = compute_simhash(base_doc, hash_bits=64, shingle_size=3)
            h_near = compute_simhash(near_doc, hash_bits=64, shingle_size=3)
            h_unrelated = compute_simhash(unrelated_doc, hash_bits=64, shingle_size=3)

            dist_near = hamming_distance(h_base, h_near)
            dist_unrelated = hamming_distance(h_base, h_unrelated)

            near_distances.append(dist_near)
            unrelated_distances.append(dist_unrelated)

            # Verify Jaccard correlation: near doc has higher Jaccard similarity than unrelated
            jaccard_near = _jaccard_similarity(base_doc, near_doc, k=3)
            jaccard_unrelated = _jaccard_similarity(base_doc, unrelated_doc, k=3)
            assert jaccard_near > jaccard_unrelated

        avg_near = sum(near_distances) / len(near_distances)
        avg_unrelated = sum(unrelated_distances) / len(unrelated_distances)

        # In a 64-bit fingerprint:
        # Unrelated texts typically differ in ~30-34 bits (near 50% random coin flips)
        # Near-duplicate texts should differ in significantly fewer bits (e.g. < 15 bits)
        assert avg_near < (avg_unrelated * 0.5), (
            f"Expected avg near ({avg_near}) < 0.5 * avg unrelated ({avg_unrelated})"
        )
