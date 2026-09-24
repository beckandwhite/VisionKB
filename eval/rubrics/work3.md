# Rubric — work3 (classification)

**Prompt under test:** `"Classify this picture. Return JSON: {\"class\": \"...\", \"confidence\": 0}."`
(The class is expected to be one token from the config `TAG_LIST`.)
**Candidate output:** `{"class": "<tag>", "confidence": <0..1>}`.
**Judge sees:** the candidate `class` + `confidence`, the allowed `TAG_LIST`, and
operator `expected_notes` (ideally the correct/acceptable tag(s) for the image).
**Never the image itself.**

Score classification on a **1–5** scale, weighing:

- **Correctness** — is the chosen `class` the best fit (or an acceptable
  near-match) for the image, per `expected_notes`?
- **Validity** — is `class` actually a token from `TAG_LIST`? An off-list or
  malformed class is penalized even if semantically close.
- **Confidence calibration** — high confidence on a wrong class, or very low
  confidence on a correct one, is penalized mildly.

## Score anchors

| Score | Meaning |
|---|---|
| 5 | Correct, on-list tag; confidence sensibly calibrated. |
| 4 | Acceptable near-match on-list tag (right scene family), or correct tag with slightly off confidence. |
| 3 | Plausible but not the best tag, or correct tag reported with clearly miscalibrated confidence. |
| 2 | Wrong tag, or an off-list / malformed class that is nonetheless in the right ballpark. |
| 1 | Wrong and off-list / malformed, or confidently wrong. |

## Output format (strict JSON)

```json
{"score": 1, "rationale": "state expected tag vs chosen, and on-list validity"}
```
