# ADR-0198: Evidence-based conversation tasks

Status: accepted

## Problem

Jarvis previously preserved displayed conversation text and attachments, while Google
tool identifiers disappeared behind the generated summary. A short confirmation could
repeat an offer, and an ordinal follow-up could lose the actual item behind its number.
Technical execution success also did not establish that a compound request was fulfilled.

## Decision

Keep inference on NVIDIA and extend the existing encrypted conversation header with a
bounded task checkpoint. Existing encrypted rows load with a null checkpoint; no schema
migration or plaintext table is required. The checkpoint is updated atomically with the
user/assistant exchange, excluded from conversation IPC responses, and follows the
existing namespace, trash, deletion and owner-voice access rules.

Checkpoint state includes the current intent, explicit Google workflow phases, pending
action and at most three verified observations. Each observation retains a bounded
excerpt and at most twenty references. Credentials are sanitized before persistence.
Google execution observations include the sanitized arguments of the consumed
authorization, stored as bounded JSON evidence in the checkpoint. Audit records retain
only execution size/hash as before; these arguments do not enter public IPC responses.
The model receives a separate valid JSON projection capped at 8 KiB, preserving current
request priority and treating all references as untrusted data. This is a byte budget,
not a claim of exact model-specific token accounting.

References come exclusively from verified Google results. Recognize structured MCP
envelopes and the pinned workspace-mcp 1.26.1 Gmail/Drive list formats. Preserve ordinal
positions and reject ambiguous or corrupted prose. Calendar prose has insufficient
calendar provenance and does not supply automatic ordinal references. No identifier is
invented from an assistant summary. Lists older than thirty minutes require clarification.

An explicit ordinal read selects only its detail tool and must propose the exact verified
identifier. The broker still issues the concrete approval. Duplicate call IDs or identical
tool/argument proposals fail before execution. Short confirmations with history are
buffered only until a repeated offer is ruled out; one inference repair is allowed. No
tool is executed during repair, and persistent repetition is an incomplete response.

Workflows track up to four explicit Google clauses. One observed result can satisfy only
one pending phase. A follow-up can resume a pending write phase only when it came from
the user's explicit compound request and an earlier phase has verified evidence. A
standalone assistant write offer cannot grant that scope. Each action needs its own
broker approval; this does not introduce an unattended workflow scheduler.

Pending phases are reported explicitly. An additional NVIDIA semantic judge runs only
after all recognized phases of a compound task have evidence. Its strict JSON verdict
cannot authorize tools, modify verified execution facts or retry an action. Failure to
judge preserves the real action result and reports that the complete goal could not be
checked. A verified checkpoint describes recognized phase evidence; it does not override
a negative or unavailable semantic verdict.
The judge sees observed operations and approved arguments, never the overall verified
label or phase-completion claims. Deterministic checks reject missing/truncated evidence
and a missing or mismatched draft recipient before invoking a semantic model. Continuing
a workflow cannot replay a completed tool/argument pair; one inference-only repair is
allowed before the next concrete broker approval. Explicit new requests may repeat work.

Approved jobs retain the checkpoint used during proposal. An intervening conversation
turn cannot substitute its objective when the approved result is persisted.

## Validation

Deterministic regressions cover Spanish confirmations, ordinal reads, missing/stale IDs,
ambiguous domains, pinned peer formats, encrypted reload after history eviction, IPC
exclusion, duplicate proposals, partial goals, selective judging, and approval boundaries.
An integrated job test searches, approves, resolves the second item, approves its detail
read and checks two exact executions. Optional NVIDIA probes use synthetic data and stop
at broker approval; they never access Gmail or execute an MCP action.

## Limits

This change addresses conversation and orchestration, not acoustic wake-word activation.
Unknown compound clauses remain outside deterministic workflow verification. Exact token
accounting and automatic multi-step scheduling are separate work. The implementation is
original Python code informed by the public MiMo-Code execution/checkpoint/goal patterns;
it does not install MiMo, introduce Xiaomi inference or replace the native interface.
