# Report specification

Requirements supplied by the human on 25 October 2026. Each part's report
(`docs/reports/part-N.md`) must be readable without watching it get built, sized
for several hours of careful reading, and contain these sections in this order:

1. **Executive summary** (half a page): what this part built, in plain language,
   and why it matters to the product.
2. **Concepts from scratch**: every new technology, algorithm, or pattern; analogy,
   real mechanism, then why WARDEN needs it. This replaces hand-coding as the
   learning vehicle; explain thoroughly.
3. **Design decisions and the road not taken**: every non-obvious choice,
   alternatives seriously considered, reasons for the choice and honest trade-offs.
4. **A guided code tour**: selected files and the 20–40 lines that matter in each,
   with line-by-line or block-by-block explanation; not a complete file dump.
5. **Interview question bank**: at least 25 plausible technical questions, each
   with a 3–6 sentence model answer; at least five gotchas probing a trade-off or
   known limitation honestly.
6. **Try it yourself**: 3–5 standalone exercises reproducing core ideas in isolated
   scratch scripts outside the main repository, each doable in under an hour.
7. **Known limitations and what a later part must revisit.**
8. **Updated `docs/AUTHORSHIP.md`**: include the actual diff in the report.
9. **Handoff**: a tight current-state summary for a cold-start future session.
