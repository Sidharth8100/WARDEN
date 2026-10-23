"""Tests for feature extraction and weighted linear classification in warden_compare."""

from warden_compare.classify import (
    FeatureVector,
    classify,
    extract_features,
    load_classifier_config,
    score,
)
from warden_compare.normalize import normalize


def test_feature_extraction_numeric_price_change():
    html_old = b"<html><body><div class='price'>$99.99</div></body></html>"
    html_new = b"<html><body><div class='price'>$109.99</div></body></html>"

    doc_old = normalize(html_old)
    doc_new = normalize(html_new)

    features = extract_features(doc_old, doc_new)
    assert features.is_numeric_only is True
    assert features.text_change_ratio > 0.0


def test_feature_extraction_date_timestamp_noise():
    html_old = b"<html><body><span data-timestamp='100'>Updated 2 hours ago</span></body></html>"
    html_new = b"<html><body><span data-timestamp='200'>Updated 5 hours ago</span></body></html>"

    doc_old = normalize(html_old)
    doc_new = normalize(html_new)

    features = extract_features(doc_old, doc_new)
    assert features.date_timestamp_hit_count >= 1


def test_weighted_scoring_and_classification():
    weights, threshold = load_classifier_config()
    assert threshold > 0.0
    assert "text_change_ratio" in weights

    # High signal feature vector
    signal_features = FeatureVector(
        text_change_ratio=0.6,
        is_numeric_only=True,
        date_timestamp_hit_count=0,
        dom_depth_score=0.8,
        is_ad_or_banner=False,
        simhash_distance=20,
        tfidf_similarity=0.2,
        visual_ssim=0.8,
        visual_changed_area=0.25,
        visual_bbox_count=2,
    )

    s = score(signal_features, weights)
    res = classify(signal_features, weights, threshold)
    assert s >= threshold
    assert res.meaningful is True
    assert res.score == round(s, 4)
    assert "text_change_ratio" in res.features

    # Pure noise feature vector (banner, timestamps, no real text change)
    noise_features = FeatureVector(
        text_change_ratio=0.02,
        is_numeric_only=False,
        date_timestamp_hit_count=4,
        dom_depth_score=0.2,
        is_ad_or_banner=True,
        simhash_distance=2,
        tfidf_similarity=0.98,
        visual_ssim=0.99,
        visual_changed_area=0.01,
        visual_bbox_count=1,
    )
    s_noise = score(noise_features, weights)
    res_noise = classify(noise_features, weights, threshold)
    assert s_noise < threshold
    assert res_noise.meaningful is False


def test_content_addition_and_deletion_remain_signal_in_long_lists():
    before = (
        b"<html><body><ul>"
        + b"".join(f"<li>Existing role number {index}</li>".encode() for index in range(100))
        + b"</ul></body></html>"
    )
    after = before.replace(b"</ul>", b"<li>Distributed Systems Engineer</li></ul>")
    weights, threshold = load_classifier_config()
    for old, new in [(before, after), (after, before)]:
        features = extract_features(normalize(old), normalize(new))
        assert features.has_content_structure_change
        assert classify(features, weights, threshold).meaningful


def test_structure_only_wrapper_change_is_not_content_signal():
    old = normalize(b"<html><body><div>Stable content</div></body></html>")
    new = normalize(b"<html><body><section>Stable content</section></body></html>")
    features = extract_features(old, new)
    weights, threshold = load_classifier_config()
    assert not features.has_content_structure_change
    assert not classify(features, weights, threshold).meaningful


def test_small_timestamp_update_does_not_gain_structural_signal():
    old = normalize(
        b"<ul><li>Engineer posted 2 hours ago</li><li>Manager posted 1 hours ago</li></ul>"
    )
    new = normalize(
        b"<ul><li>Engineer posted 3 hours ago</li><li>Manager posted 2 hours ago</li></ul>"
    )
    features = extract_features(old, new)
    weights, threshold = load_classifier_config()
    assert not features.has_content_structure_change
    assert not classify(features, weights, threshold).meaningful
