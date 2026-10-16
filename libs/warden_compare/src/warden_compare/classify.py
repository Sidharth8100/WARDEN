"""Feature extraction, student TF-IDF stub, and rule-weighted linear classifier v1."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from warden_compare.normalize import NormalizedDoc
from warden_compare.simhash import compute_simhash, hamming_distance
from warden_compare.treediff import diff_trees, hash_tree
from warden_compare.visualdiff import diff_screenshots

CONFIG_PATH = Path(__file__).parent / "classifier_config.json"

DATE_TIME_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date
    re.compile(r"\b\d{10}(?:\.\d+)?\b"),  # Unix timestamp
    re.compile(
        r"\b\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
]


def tfidf_cosine_similarity(
    doc_a_tokens: list[str],
    doc_b_tokens: list[str],
    corpus_tokenized: list[list[str]],
) -> float:
    """[YOU IMPLEMENT] Compute the cosine similarity between doc_a and
    doc_b's TF-IDF vectors, where IDF is computed from `corpus_tokenized`
    (the set of documents establishing how common/rare each term is —
    in this project, the corpus is the monitor's own snapshot history).
    Return a value in [0.0, 1.0] (1.0 = identical direction, meaning
    very similar; cosine similarity is technically in [-1,1] but
    TF-IDF weights are non-negative, so it never goes below 0 here —
    confirm this is true and note it).

    Do not use scikit-learn, gensim, or any other vectorization
    library in this function's implementation.

    Hint L1: TF (term frequency) is just "how often does this term
    appear in THIS document". IDF (inverse document frequency) is
    "how rare is this term ACROSS THE WHOLE CORPUS" — common words
    like 'the' get a low IDF (they appear everywhere, so they're not
    discriminating), rare words get a high IDF. TF-IDF for a term in
    a document is tf * idf. Cosine similarity between two vectors is
    their dot product divided by the product of their magnitudes.
    Hint L2: a standard formula is idf(t) = ln(N / (1 + df(t))) where
    N is corpus size and df(t) is the number of corpus documents
    containing t at least once (the +1 avoids division by zero for a
    term that appears in every single document). Build each
    document's TF-IDF vector as a dict {term: weight} (a sparse
    representation — do not materialize a dense vector over the full
    vocabulary, that wastes memory for no benefit here). Cosine
    similarity over two sparse dicts: sum the product of shared
    terms' weights, divide by (sqrt(sum of a's weights squared) *
    sqrt(sum of b's weights squared)).
    Hint L3: handle the zero-vector edge case explicitly (an empty
    document, or a document with no terms that survive — e.g. if you
    choose to drop terms appearing in EVERY document as pure stopword
    noise, which is a legitimate design choice but must be decided
    and documented, not accidental) — cosine similarity is undefined
    (0/0) when either vector has zero magnitude; decide and document
    what you return in that case (0.0 is a reasonable, common choice).
    """
    raise NotImplementedError("see docstring")


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
            "text_change_ratio": 0.35,
            "is_numeric_only": 0.25,
            "date_timestamp_hit_count": -0.30,
            "dom_depth_score": 0.15,
            "is_ad_or_banner": -0.40,
            "simhash_difference_ratio": 0.20,
            "tfidf_dissimilarity": 0.25,
            "visual_change_coverage": 0.20,
        }
        return default_weights, 0.45

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data.get("weights", {}), float(data.get("threshold", 0.45))


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

    # 1. Text change ratio based on diff tokens
    # Reuses granular diffs from Myers edit script if implemented, otherwise sequence matcher
    diff_tokens: list[str] = []
    text_change_ratio = 0.0
    try:
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
    except (NotImplementedError, Exception):
        import difflib

        sm = difflib.SequenceMatcher(None, old_tokens, new_tokens)
        changed_count = 0
        deleted_toks: list[str] = []
        inserted_toks: list[str] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                changed_count += max(i2 - i1, j2 - j1)
            if tag in ("delete", "replace"):
                deleted_toks.extend(old_tokens[i1:i2])
            if tag in ("insert", "replace"):
                inserted_toks.extend(new_tokens[j1:j2])
        max_tokens = max(len(old_tokens), len(new_tokens), 1)
        text_change_ratio = min(1.0, changed_count / max_tokens)
        diff_tokens = deleted_toks + inserted_toks

    # 2. Numeric-only change detection
    # Check if differences consist purely of digits, currency symbols, and delimiters
    is_numeric = False
    if diff_tokens:
        is_numeric = all(bool(re.match(r"^[\d\.,\$\€\£\¥\+\-\%]+$", tok)) for tok in diff_tokens)

    # 3. Date / timestamp hit count in changed text
    changed_str = " ".join(diff_tokens)
    date_hits = sum(len(p.findall(changed_str)) for p in DATE_TIME_PATTERNS)
    # Also evaluate differences in full text pattern matches (catches "2 days ago" -> "3 days ago")
    for p in DATE_TIME_PATTERNS:
        matches_old = set(p.findall(old_text))
        matches_new = set(p.findall(new_text))
        date_hits += len(matches_old ^ matches_new)

    # If numeric changes occurred inside a date ticker, disqualify as numeric-only price change
    if date_hits > 0 and text_change_ratio < 0.20:
        is_numeric = False

    # 4. DOM position & ad/banner detection from structural tree diff
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
    # Normalize depth score: 1.0 = shallow (near root), 0.0 = deep
    dom_depth_score = max(0.0, min(1.0, 1.0 - (avg_depth / 10.0)))

    # 5. SimHash Hamming distance
    simhash_dist = 0
    try:
        h1 = compute_simhash(old_text)
        h2 = compute_simhash(new_text)
        simhash_dist = hamming_distance(h1, h2)
    except NotImplementedError:
        simhash_dist = 0

    # 6. TF-IDF cosine similarity
    tfidf_sim = 1.0 if old_text == new_text else 0.0
    try:
        tokens_a = old_tokens
        tokens_b = new_tokens
        full_corpus = corpus if corpus is not None else [tokens_a, tokens_b]
        tfidf_sim = tfidf_cosine_similarity(tokens_a, tokens_b, full_corpus)
    except NotImplementedError:
        tfidf_sim = 1.0 if old_text == new_text else 0.5

    # 7. Visual SSIM & bounding box coverage (tier-2 screenshots only)
    visual_ssim = None
    visual_coverage = None
    visual_boxes_count = None
    if old_screenshot and new_screenshot:
        try:
            v_res = diff_screenshots(old_screenshot, new_screenshot)
            visual_ssim = v_res.ssim_score
            visual_coverage = v_res.coverage_ratio
            visual_boxes_count = len(v_res.bounding_boxes)
        except (NotImplementedError, Exception):
            visual_ssim = None
            visual_coverage = None
            visual_boxes_count = None

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
    )


def score(features: FeatureVector, weights: dict[str, float]) -> float:
    """Compute transparent weighted linear combination of normalized feature values in [0, 1]."""
    raw_score = 0.0

    # 1. Text change ratio (0 to 1)
    raw_score += weights.get("text_change_ratio", 0.0) * features.text_change_ratio

    # 2. Numeric only (+ weight if true)
    if features.is_numeric_only:
        raw_score += weights.get("is_numeric_only", 0.0)

    # 3. Date / timestamp hit count (negative weight, clamped 0 to 1)
    # When text change ratio is high (>= 0.20), date mentions in newly added listings
    # are subordinate to major content changes rather than isolated ticker noise.
    date_noise_factor = max(0.0, 1.0 - (features.text_change_ratio * 4.0))
    clamped_date_hits = min(1.0, features.date_timestamp_hit_count / 3.0) * date_noise_factor
    raw_score += weights.get("date_timestamp_hit_count", 0.0) * clamped_date_hits

    # 4. DOM depth score (0 to 1)
    raw_score += weights.get("dom_depth_score", 0.0) * features.dom_depth_score

    # 5. Ad or banner (negative weight if true)
    if features.is_ad_or_banner:
        raw_score += weights.get("is_ad_or_banner", 0.0)

    # 6. SimHash difference ratio (dist / 64)
    simhash_diff_ratio = min(1.0, features.simhash_distance / 64.0)
    raw_score += weights.get("simhash_difference_ratio", 0.0) * simhash_diff_ratio

    # 7. TF-IDF dissimilarity (1 - similarity)
    tfidf_dissim = max(0.0, min(1.0, 1.0 - features.tfidf_similarity))
    raw_score += weights.get("tfidf_dissimilarity", 0.0) * tfidf_dissim

    # 8. Visual change coverage
    if features.visual_changed_area is not None:
        raw_score += weights.get("visual_change_coverage", 0.0) * min(
            1.0, features.visual_changed_area
        )

    # Bound overall score to [0.0, 1.0]
    return max(0.0, min(1.0, raw_score))


def classify(
    features: FeatureVector,
    weights: dict[str, float],
    threshold: float,
) -> ClassificationResult:
    """Classify candidate change features as meaningful or noise."""
    calc_score = score(features, weights)
    meaningful = calc_score >= threshold
    return ClassificationResult(
        score=round(calc_score, 4),
        meaningful=meaningful,
        features=asdict(features),
    )
