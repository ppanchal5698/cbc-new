# Golden set (§5.10)

The evaluation harness lives in `run_eval.py` (built in Phase 6) and is driven by
`manifest.yaml` plus the labels in `labels/`.

**The PDFs are not in git.** Client bid sets are confidential and large; git holds
the manifest and the labels, and the documents live in S3 (§8.2).

## What the previous file here did

`test_extraction_eval.py` computed:

```python
true_positives += len(expected)     # then asserted recall > 0.95
```

It hardcoded a perfect score and asserted against it, so it passed unconditionally
while standing in for the CI quality gate. It has been deleted rather than fixed —
a test that cannot fail is worse than no test, because it produces a green build
that means nothing.

## What replaces it

Per-field metrics, not an aggregate (§5.10):

| Metric | Why |
|---|---|
| Precision | Of the values produced, how many were right |
| Recall | Of the values present in the document, how many were found |
| **Absent-accuracy** | Of the fields genuinely absent, how many were correctly reported absent rather than hallucinated. **This is the metric NFR-2 actually cares about** and it is invisible in a precision/recall summary |
| Citation validity | Share of cited elements that exist and ground the value |
| Escape rate at threshold | Wrong values that were *not* flagged |
| Cost and latency per document | Guards against a prompt change that quietly triples spend |

Plus classifier recall from §4 triage, weighted far above precision: a false
positive costs $0.015, a false negative costs a missing opening (Risk R12).

CI gates on regression against a recorded baseline.
