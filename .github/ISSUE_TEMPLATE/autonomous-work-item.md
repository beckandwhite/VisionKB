---
name: Autonomous work item
about: A groomed item for the "Ready to be picked up by AI" column — precise enough for a model to execute end-to-end.
title: "Improve <area> — <n.n>: <short title>"
labels: []
---

**Parent:** #<n> · **Depends on:** <#n or none> · **Run order:** <FIRST / after #n>

<!-- Add exactly ONE size label after creating: size:S, size:M, or size:L.
     S = Gemma 4 12B (Mac mini 16GB) · M = Qwen3 ~27–30B (48GB) · L = cloud, >128K context.
     See Plans/autonomous-sizing.md. -->

> **Autonomous-run spec.** Follow the steps exactly. Do only what is listed. If a
> step is ambiguous, **STOP** and log the question (see Decision logging below)
> rather than guessing.

## Goal

<One paragraph: what changes and why. State any feature that does NOT move.>

## Files you may touch

- `path/to/file` (what you may change in it)
- **create** `new/file` (purpose)

Do **NOT** touch <files/areas that are out of bounds for this item>.

## Locked decisions (from grooming)

- <Any decision already made, so the executor doesn't re-open it.>

## Steps

1. **`file`** — <precise instruction>.
2. …

## Verify

- <Exact command(s) and the observable result that means success.>

## Decision logging (autonomous runs)

Record any judgment call, assumption, or deviation from these steps in
[`DECISIONS.md`](https://github.com/beckandwhite/VisionKB/blob/main/DECISIONS.md),
keyed by this issue number. If a step is ambiguous, STOP and log the question.

## Out of scope

- <Adjacent work that belongs to another item.>
