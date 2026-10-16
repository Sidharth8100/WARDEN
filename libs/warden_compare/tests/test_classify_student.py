"""Student test suite for Stub #4: TF-IDF cosine similarity.

STUDENT STUB TEST:
Tagged with @pytest.mark.student_todo. Excluded from default `make check`.
Run explicitly via `make check-todos`.
"""

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from warden_compare.classify import tfidf_cosine_similarity


def _sklearn_tfidf_oracle(doc_a: str, doc_b: str, corpus: list[str]) -> float:
    """Independent reference oracle using scikit-learn TfidfVectorizer and cosine_similarity.

    TEST DEPENDENCY NOTE:
    scikit-learn is restricted to test oracles in dev dependency groups only.
    No application code under src/ may import sklearn.
    """
    vectorizer = TfidfVectorizer(smooth_idf=True, norm="l2")
    vectorizer.fit(corpus)
    vecs = vectorizer.transform([doc_a, doc_b])
    sim = cosine_similarity(vecs[0:1], vecs[1:2])[0][0]
    return float(sim)


@pytest.mark.student_todo
class TestTfidfCosineSimilarityStudent:
    """Matrix of reference oracle comparisons and edge cases for TF-IDF cosine similarity."""

    def test_identical_documents_produce_near_one_similarity(self):
        doc = ["distributed", "systems", "consensus"]
        corpus = [doc, ["other", "topics"]]
        sim = tfidf_cosine_similarity(doc, doc, corpus)
        assert sim == pytest.approx(1.0, abs=1e-3)

    def test_completely_disjoint_vocabularies_produce_zero_similarity(self):
        doc_a = ["alpha", "beta"]
        doc_b = ["gamma", "delta"]
        corpus = [doc_a, doc_b]
        sim = tfidf_cosine_similarity(doc_a, doc_b, corpus)
        assert sim == pytest.approx(0.0, abs=1e-5)

    def test_empty_document_edge_case(self):
        """Empty documents have zero magnitude: cosine similarity is safely 0.0, not a crash."""
        doc_a: list[str] = []
        doc_b = ["word"]
        corpus = [doc_a, doc_b]
        sim = tfidf_cosine_similarity(doc_a, doc_b, corpus)
        assert sim == 0.0

    @pytest.mark.parametrize(
        ("doc_a", "doc_b", "extra_docs"),
        [
            ("apple banana cherry", "apple banana date", ["apple fig grape"]),
            ("machine learning models", "deep learning neural models", ["relational database sql"]),
            ("fastapi async python web", "django synchronous python", ["rust system programming"]),
            ("postgres index btree gin", "postgres table scan heap", ["redis memory key value"]),
            ("network partition raft", "network latency paxos", ["single node disk fail"]),
            ("docker compose container", "kubernetes pod container", ["bare metal server"]),
            ("oauth jwt bearer token", "basic auth password header", ["cookie session storage"]),
            ("html dom parser tree", "xml dom element attribute", ["json serializer parser"]),
            ("unit test coverage mock", "integration test live db", ["property hypothesis fuzz"]),
            (
                "continuous delivery deploy",
                "continuous integration build",
                ["git pull request merge"],
            ),
        ],
    )
    def test_ten_hand_built_documents_against_sklearn_oracle(
        self, doc_a: str, doc_b: str, extra_docs: list[str]
    ):
        """Cross-check 10 hand-built document sets against scikit-learn oracle.

        NOTE ON DIFFERENCES:
        Standard textbooks and implementations vary in IDF smoothing (e.g. ln(N/(1+df)) vs
        sklearn's ln((1+N)/(1+df)) + 1). Agreement is expected within a tight tolerance (0.15)
        reflecting smoothing formula conventions rather than algorithmic divergence.
        """
        full_corpus_str = [doc_a, doc_b, *extra_docs]
        corpus_tokenized = [s.split() for s in full_corpus_str]

        student_sim = tfidf_cosine_similarity(doc_a.split(), doc_b.split(), corpus_tokenized)
        oracle_sim = _sklearn_tfidf_oracle(doc_a, doc_b, full_corpus_str)

        assert 0.0 <= student_sim <= 1.0
        assert student_sim == pytest.approx(oracle_sim, abs=0.15)

    @settings(max_examples=15, deadline=None)
    @given(
        words_a=st.lists(st.sampled_from(["cat", "dog", "fish", "bird"]), min_size=1, max_size=4),
        words_b=st.lists(st.sampled_from(["dog", "bird", "lion", "bear"]), min_size=1, max_size=4),
    )
    def test_hypothesis_corpus_agreement(self, words_a: list[str], words_b: list[str]):
        corpus = [words_a, words_b, ["elephant", "whale"]]
        sim = tfidf_cosine_similarity(words_a, words_b, corpus)
        assert 0.0 <= sim <= 1.0
