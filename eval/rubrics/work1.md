# Rubric — work1 (generic vision description)

**Prompt under test:** `"What is on this picture? Describe the important visible content."`
**Candidate output:** free-text `answer` string.
**Judge sees:** the candidate `answer` + any operator `expected_notes` for the
image. **Never the image itself.**

Score the description on a **1–5** scale, weighing:

- **Accuracy** — does it describe what is actually in the image (per
  `expected_notes`) without inventing content?
- **Coverage of the important content** — are the salient elements present, not
  just background trivia?
- **No hallucination** — invented objects, text, people, or places are the
  heaviest penalty.
- **Usefulness / conciseness** — informative without rambling.

## Score anchors

| Score | Meaning |
|---|---|
| 5 | Accurate, covers all important content, no hallucination, concise. |
| 4 | Accurate and mostly complete; minor omission or minor vagueness. |
| 3 | Broadly right but misses a key element or is too vague to be useful. |
| 2 | Partially wrong, or a notable hallucination alongside some correct content. |
| 1 | Mostly wrong, or dominated by hallucinated content. |

## Output format (strict JSON)

```json
{"score": 1, "rationale": "one or two sentences citing what was right/wrong"}
```
