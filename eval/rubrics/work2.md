# Rubric — work2 (OCR extraction)

**Prompt under test:** `"Extract all visible text from this picture. Return JSON: {\"text\": [\"line\"]}."`
**Candidate output:** `{"text": ["line", ...]}` — a list of transcribed lines.
**Judge sees:** the candidate lines + operator `expected_notes` (ideally the
ground-truth visible text for the image). **Never the image itself.**

Score OCR fidelity on a **1–5** scale, weighing:

- **Recall** — is all visible text captured? Missing lines are penalized.
- **Transcription accuracy** — characters, numbers, punctuation, casing correct?
- **No invented text** — lines not present in the image are the heaviest penalty
  (worse than a missed line).
- **Reasonable segmentation/order** — lines roughly follow reading order; minor
  splitting/merging is tolerated.

## Score anchors

| Score | Meaning |
|---|---|
| 5 | All visible text captured and transcribed correctly; nothing invented. |
| 4 | Nearly complete; a few minor character errors or one trivial omission. |
| 3 | Gets the gist but misses a meaningful chunk, or several transcription errors. |
| 2 | Substantial text missing or wrong, or some invented lines. |
| 1 | Largely wrong, empty when text exists, or dominated by invented text. |

## Output format (strict JSON)

```json
{"score": 1, "rationale": "cite missed/invented/mistranscribed lines"}
```
