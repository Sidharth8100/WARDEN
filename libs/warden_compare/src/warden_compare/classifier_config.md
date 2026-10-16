# WARDEN Classifier v1 Configuration Rationale

Classifier v1 computes a transparent, weighted linear score in $[0.0, 1.0]$ across extracted structural and textual features. Tuning the classifier consists of adjusting weights in `classifier_config.json` without altering application logic.

## Feature Weights and Signs

| Feature | Weight | Sign | Rationale |
| :--- | :--- | :--- | :--- |
| `text_change_ratio` | `+0.35` | Positive | Substantial textual alterations strongly correlate with meaningful content edits. Normalized to $[0, 1]$. |
| `is_numeric_only` | `+0.25` | Positive | Single-token numeric/currency mutations typically represent price changes, stock counts, or inventory updates (high-signal alerts). |
| `date_timestamp_hit_count` | `-0.30` | Negative | High concentration of relative times ("2 hours ago") or timestamps indicates cosmetic tickers and chronological noise. Decreases meaningfulness score. |
| `dom_depth_score` | `+0.15` | Positive | Mutations higher in the document hierarchy (closer to root/main content) are more likely to represent page-level changes than deeply nested micro-elements. |
| `is_ad_or_banner` | `-0.40` | Negative | Mutations occurring within promotional banners, rotating carousels, or ad frames are overwhelmingly cosmetic noise. |
| `simhash_difference_ratio` | `+0.20` | Positive | Normalized Hamming distance $(\text{dist} / 64)$. Significant divergence indicates document-level semantic drift. |
| `tfidf_dissimilarity` | `+0.25` | Positive | $(1.0 - \text{cosine\_similarity})$. Changes involving rare, informative corpus tokens represent significant semantic updates. |
| `visual_change_coverage` | `+0.20` | Positive | Bounding-box area ratio from SSIM difference mask (tier-2 rendering). Large visual displacements indicate prominent layout shifts. |

## Decision Threshold

- **Threshold**: `0.45`
- A candidate change is labeled `meaningful = True` if $\text{Score} \ge 0.45$.
- A candidate change is labeled `meaningful = False` (noise) if $\text{Score} < 0.45$.
