# Autonomous Work Sizing (S / M / L)

**Date:** 2026-09-28
**Board:** GitHub Project *Vision Knowledgebase* → column **"Ready to be picked up by AI"**
**Origin:** board issue #27 — *"S/M/L for AI - work - 12b/30b/1M context"*

## Purpose

Every work item in the **Ready to be picked up by AI** column gets a **size**
that says *which model — and therefore which machine — should pick it up*. This
turns "who runs this?" from a judgment call into a label an autonomous agent can
read for itself.

> **Not the same as [`recommendedHW.md`](../recommendedHW.md).** That doc is about
> the **vision models the app runs at runtime** (Ollama, image → description).
> *This* doc is about the **coding models that execute work items** at dev time.
> Same hardware appears in both; the models and the purpose differ.

## The scheme

| Size | Model (working name) | Machine | Context | Pick this when… |
|---|---|---|---|---|
| **`size:S`** | Gemma 4 12B | Mac mini M4, 16 GB | standard | The item is small and **precise**: a tight, numbered step list, one or two files, low ambiguity, little design judgment. Mechanical edits and wiring-up-existing-code fit here. |
| **`size:M`** | Qwen3 ~27–30B | 48 GB box (MBP/Mac Pro) | standard | Moderate scope: a handful of files, a new small endpoint, or a change needing **real but bounded judgment** (drag interactions, join logic, redaction). |
| **`size:L`** | Cloud / frontier model | Cloud (limited access) | **>128 K** | Heavy or cross-cutting: many files across layers, multi-phase work, or a task whose context genuinely won't fit smaller — reserve for when S/M can't. |

### Terminology note (why the old wording clashed)

The existing #8-series issues say *"Target executor: small local model (~27B)"* —
they called **27B "small"** (small *relative to frontier*). Under this scheme
**27B is `size:M`**, and `size:S` is the 12B tier. When you see "~27B" in an older
issue body, read it as **M**. New items should use the label, not prose.

## How it's applied

- Size lives as a **GitHub label**: `size:S`, `size:M`, or `size:L`, applied to
  the issue. It is visible in `gh issue view <n>` and to any agent that reads the
  issue — so an executor can confirm *"am I the right model for this?"* before
  starting.
- Every item in **Ready to be picked up by AI** should carry exactly one size
  label. Items still in *Ideas*/*Grilling* don't need one until they're groomed.

## Self-routing rule for an autonomous executor

1. Read the issue and its `size:*` label.
2. If your model tier is **smaller** than the label, **do not start** — leave a
   comment saying so and stop. (A 12B should not attempt a `size:L`.)
3. If it matches (or you're a larger model deliberately taking a smaller item),
   proceed, following the issue's steps exactly.
4. Log judgment calls and deviations per the decision-log convention below.

## Decision-log convention

Autonomous runs record their judgment calls, assumptions, and deviations in the
repo-level append-only log [`DECISIONS.md`](../DECISIONS.md), keyed by issue
number. This keeps *why the code looks the way it does* out of the reviewer's
head and in the tree. If a step is ambiguous, **STOP and log the question**
rather than guessing.

## Adding a new autonomous work item

Use the issue template at
[`.github/ISSUE_TEMPLATE/autonomous-work-item.md`](../.github/ISSUE_TEMPLATE/autonomous-work-item.md),
which bakes in the size line, the files-you-may-touch allowlist, numbered steps,
a Verify section, and the decision-log pointer.
