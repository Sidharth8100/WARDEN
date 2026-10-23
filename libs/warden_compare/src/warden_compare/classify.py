"""Pure feature extraction, smoothed TF-IDF, and versioned rule classifier v2."""

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from warden_compare.normalize import NormalizedDoc
from warden_compare.simhash import compute_simhash, hamming_distance
from warden_compare.treediff import TreeDelete, TreeInsert, diff_trees, hash_tree
from warden_compare.visualdiff import diff_screenshots

CONFIG_PATH = Path(__file__).parent / "classifier_config.json"
CLASSIFIER_VERSION = "v2"
DATE_TIME_PATTERNS = [
    re.compile("\\b\\d{4}-\\d{2}-\\d{2}\\b"),
    re.compile("\\b\\d{10}(?:\\.\\d+)?\\b"),
    re.compile(
        "\\b\\d+\\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\\s+ago\\b",
        re.IGNORECASE,
    ),
    re.compile("\\b\\d{1,2}:\\d{2}(?::\\d{2})?\\s*(?:am|pm)?\\b", re.IGNORECASE),
    re.compile(
        "\\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2},?\\s+\\d{4}\\b",
        re.IGNORECASE,
    ),
]


def tfidf_cosine_similarity(
    doc_a_tokens: list[str], doc_b_tokens: list[str], corpus_tokenized: list[list[str]]
) -> float:
    """Smoothed TF-IDF cosine; idf=log((1+N)/(1+df))+1.

    Match the established sklearn oracle, with vocabulary learned only from the
    supplied corpus. Empty/unknown-token vectors return zero. No external
    vectorization dependency is used by the application implementation.
    """
    if not corpus_tokenized or not doc_a_tokens or (not doc_b_tokens):
        return 0.0
    frequencies: Counter[str] = Counter()
    for document in corpus_tokenized:
        frequencies.update(set(document))
    size = len(corpus_tokenized)

    def vector(tokens: list[str]) -> dict[str, float]:
        return {
            word: count * (math.log((1 + size) / (1 + frequencies[word])) + 1)
            for word, count in Counter(tokens).items()
            if word in frequencies
        }

    left, right = (vector(doc_a_tokens), vector(doc_b_tokens))
    norm = math.sqrt(sum(v * v for v in left.values()) * sum(v * v for v in right.values()))
    if norm == 0:
        return 0.0
    return min(
        1.0, max(0.0, sum((value * right.get(word, 0) for word, value in left.items())) / norm)
    )


@dataclass(frozen=True)
class FeatureVector:
    """Typed feature set extracted from normalized documents and screenshots."""

    text_change_ratio: float
    is_numeric_only: bool
    date_timestamp_hit_count: int
    dom_depth_score: float
    is_ad_or_banner: bool
    simhash_distance: int
    tfidf_similarity: float
    visual_ssim: float | None
    visual_changed_area: float | None
    visual_bbox_count: int | None
    has_content_structure_change: bool = False
    is_timestamp_only: bool = False


@dataclass(frozen=True)
class ClassificationResult:
    """Result of linear feature scoring and threshold classification."""

    score: float
    meaningful: bool
    features: dict[str, Any]


def load_classifier_config() -> tuple[dict[str, float], float]:
    """Load weights and decision threshold from classifier_config.json."""
    if not CONFIG_PATH.is_file():
        default_weights = {
            "text_change_ratio": 0.45,
            "is_numeric_only": 0.35,
            "date_timestamp_hit_count": -0.4,
            "dom_depth_score": 0.15,
            "is_ad_or_banner": -0.4,
            "simhash_difference_ratio": 0.2,
            "tfidf_dissimilarity": 0.25,
            "visual_change_coverage": 0.2,
            "has_content_structure_change": 0.35,
        }
        return (default_weights, 0.35)
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if data.get("version") != CLASSIFIER_VERSION:
        raise ValueError("classifier configuration version mismatch")
    return (data["weights"], float(data["threshold"]))


def extract_features(
    old: NormalizedDoc,
    new: NormalizedDoc,
    old_screenshot: bytes | None = None,
    new_screenshot: bytes | None = None,
    corpus: list[list[str]] | None = None,
) -> FeatureVector:
    """Extract structural, textual, and visual diff features between two document versions."""
    old_text = old.text
    new_text = new.text
    old_tokens = old_text.split()
    new_tokens = new_text.split()
    diff_tokens: list[str] = []
    text_change_ratio = 0.0
    from warden_compare.textdiff import Delete, Insert, Keep, compute_edit_script

    script = compute_edit_script(old_tokens, new_tokens)
    non_keep_count = sum(
        len(op.items) if isinstance(op, Insert) else op.n
        for op in script
        if not isinstance(op, Keep)
    )
    text_change_ratio = min(1.0, non_keep_count / max(len(old_tokens), len(new_tokens), 1))
    for op in script:
        if isinstance(op, Insert):
            diff_tokens.extend(op.items)
        elif isinstance(op, Delete):
            pass
    is_numeric = False
    if diff_tokens:
        is_numeric = all(
            bool(re.match("^[\\d\\.,\\$\\€\\£\\¥\\+\\-\\%]+$", tok)) for tok in diff_tokens
        )
    changed_str = " ".join(diff_tokens)
    date_hits = sum(len(p.findall(changed_str)) for p in DATE_TIME_PATTERNS)
    for p in DATE_TIME_PATTERNS:
        matches_old = set(p.findall(old_text))
        matches_new = set(p.findall(new_text))
        date_hits += len(matches_old ^ matches_new)
    if date_hits > 0 and text_change_ratio < 0.2:
        is_numeric = False

    # Context matters: changing "2 hours ago" to "3 hours ago" inserts a
    # number, but is not a price/count change. Compare the surrounding content
    # after replacing entire recognized temporal expressions, at any text ratio.
    def without_timestamps(value: str) -> str:
        for pattern in DATE_TIME_PATTERNS:
            value = pattern.sub("<time>", value)
        return value

    timestamp_only = (
        old_text != new_text
        and date_hits > 0
        and without_timestamps(old_text) == without_timestamps(new_text)
    )
    if timestamp_only:
        is_numeric = False
    h_old = hash_tree(old.dom)
    h_new = hash_tree(new.dom)
    tree_ops = diff_trees(h_old, h_new)
    depth_scores = []
    ad_or_banner_hit = False
    for tree_op in tree_ops:
        path = getattr(tree_op, "path", "")
        depth = path.count("/")
        depth_scores.append(depth)
        lower_path = path.lower()
        if any(w in lower_path for w in ("ad", "banner", "promo", "sponsor", "iframe")):
            ad_or_banner_hit = True
    avg_depth = sum(depth_scores) / len(depth_scores) if depth_scores else 1.0
    dom_depth_score = max(0.0, min(1.0, 1.0 - avg_depth / 10.0))
    simhash_dist = 0
    h1 = compute_simhash(old_text)
    h2 = compute_simhash(new_text)
    simhash_dist = hamming_distance(h1, h2)
    tfidf_sim = 1.0 if old_text == new_text else 0.0
    tokens_a = old_tokens
    tokens_b = new_tokens
    full_corpus = corpus if corpus is not None else [tokens_a, tokens_b]
    tfidf_sim = tfidf_cosine_similarity(tokens_a, tokens_b, full_corpus)
    visual_ssim = None
    visual_coverage = None
    visual_boxes_count = None
    if old_screenshot and new_screenshot:
        v_res = diff_screenshots(old_screenshot, new_screenshot)
        visual_ssim = v_res.ssim_score
        visual_coverage = v_res.coverage_ratio
        visual_boxes_count = len(v_res.bounding_boxes)
    return FeatureVector(
        text_change_ratio=round(text_change_ratio, 4),
        is_numeric_only=is_numeric,
        date_timestamp_hit_count=date_hits,
        dom_depth_score=round(dom_depth_score, 4),
        is_ad_or_banner=ad_or_banner_hit,
        simhash_distance=simhash_dist,
        tfidf_similarity=round(tfidf_sim, 4),
        visual_ssim=round(visual_ssim, 4) if visual_ssim is not None else None,
        visual_changed_area=round(visual_coverage, 4) if visual_coverage is not None else None,
        visual_bbox_count=visual_boxes_count,
        has_content_structure_change=old_text != new_text
        and any(isinstance(op, TreeInsert | TreeDelete) for op in tree_ops),
        is_timestamp_only=timestamp_only,
    )


def score(features: FeatureVector, weights: dict[str, float]) -> float:
    """Compute transparent weighted linear combination of normalized feature values in [0, 1]."""
    if features.is_timestamp_only:
        return 0.0
    raw_score = 0.0
    raw_score += weights.get("text_change_ratio", 0.0) * features.text_change_ratio
    if features.is_numeric_only:
        raw_score += weights.get("is_numeric_only", 0.0)
    date_noise_factor = max(0.0, 1.0 - features.text_change_ratio * 4.0)
    clamped_date_hits = min(1.0, features.date_timestamp_hit_count / 3.0) * date_noise_factor
    raw_score += weights.get("date_timestamp_hit_count", 0.0) * clamped_date_hits
    raw_score += weights.get("dom_depth_score", 0.0) * features.dom_depth_score
    if features.is_ad_or_banner:
        raw_score += weights.get("is_ad_or_banner", 0.0)
    if features.has_content_structure_change:
        raw_score += weights.get("has_content_structure_change", 0.0)
    simhash_diff_ratio = min(1.0, features.simhash_distance / 64.0)
    raw_score += weights.get("simhash_difference_ratio", 0.0) * simhash_diff_ratio
    tfidf_dissim = max(0.0, min(1.0, 1.0 - features.tfidf_similarity))
    raw_score += weights.get("tfidf_dissimilarity", 0.0) * tfidf_dissim
    if features.visual_changed_area is not None:
        raw_score += weights.get("visual_change_coverage", 0.0) * min(
            1.0, features.visual_changed_area
        )
    return max(0.0, min(1.0, raw_score))


def classify(
    features: FeatureVector, weights: dict[str, float], threshold: float
) -> ClassificationResult:
    """Classify candidate change features as meaningful or noise."""
    calc_score = score(features, weights)
    meaningful = calc_score >= threshold
    return ClassificationResult(
        score=round(calc_score, 4), meaningful=meaningful, features=asdict(features)
    )
