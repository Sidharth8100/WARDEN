# Classifier v2: explicit evidence for content topology changes

The classifier is a transparent weighted rule, not a trained probabilistic model.
A score is a ranking signal, not a calibrated probability. Configuration declares
version `v2`; the loader rejects a mismatched version, and events persist that
version so old decisions remain distinguishable.

| Feature | Weight | Reason |
|---|---:|---|
| Text change ratio | +0.45 | More inserted/deleted text is stronger evidence |
| Numeric-only change | +0.35 | Prices and counts often matter |
| Date/timestamp evidence | -0.40 | Small relative-time changes are frequently noise |
| DOM depth score | +0.15 | Changes near the root often have broader effect |
| Ad/banner heuristic | -0.40 | Promotional changes can be noise; this remains a heuristic |
| SimHash difference / 64 | +0.20 | Fingerprint divergence |
| 1 - TF-IDF cosine | +0.25 | Textual vocabulary divergence |
| Visual changed-area coverage | +0.20 | Screenshot evidence, when available |
| Content structure change | +0.35 | A DOM insertion/deletion with a changed normalized text value |

The threshold remains **0.35**, unchanged from the existing runtime configuration.
Default fallback weights match the packaged configuration. Timestamp penalties
are attenuated for large text changes; scores are clamped to [0, 1].

An explicit timestamp-only rule returns zero when replacing recognized temporal
expressions leaves exactly the same surrounding normalized text. This prevents
"2 hours ago" -> "3 hours ago" from being rewarded as a numeric price change on
short pages. The heuristic's limits matter: dates can be meaningful, and a
10-digit identifier can resemble a Unix timestamp. Site-specific semantics still
need selectors/rules and real labels.

Why add the final feature? With real TF-IDF/SimHash implementations, appending one
role to a long job list barely changes global text similarity. The old v1 weights
missed every list insertion/removal in a 210-pair rerun. A direct structural signal
represents an independent requirement: content entering/leaving the monitored DOM
can matter even when it is a small fraction of an otherwise stable page.

A tag/wrapper change without changed normalized text does not earn this feature.
A timestamp text edit with unchanged topology does not earn it either. Dedicated
regressions cover insertion/deletion in a 100-item list, wrapper-only changes and
relative-time ticks. This is an explicit policy decision, not a learned universal
truth: decorative text widgets can still be false positives. Page selectors,
validated rules, held-out labels and production feedback remain important.

The simulator evaluation was used to diagnose the v1 issue, so it is regression
validation, not an untouched held-out corpus. No human-labeled real website corpus
is present; neither synthetic perfection nor a green unit suite establishes real
world precision/recall. See the Part 1 report for measured results.
