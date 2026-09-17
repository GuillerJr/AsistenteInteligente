# ADR-0228: Chat management and adaptive appearance

Status: accepted

## Decision

Expose rename, fork and empty-trash actions in the native chat UI through the existing
authenticated conversation IPC service. The coordinator's owner namespace is fixed;
payloads cannot select a namespace. Rename preserves encrypted history and task state.

Fork copies the complete stored history and documents in one SQLite write transaction,
resealing each turn with a new turn/conversation identity. The original is untouched.
Only persisted exchanges are copied; active jobs, approvals and ephemeral image bytes
are not copied. Task evidence remains available, with every subsequent action still
subject to its own broker authorization. Capacity, missing/archived sources and integrity
failures roll back the whole clone. Forks have independent storage and deletion lifetimes.

Empty trash deletes only authenticated archived conversations in the owner's namespace,
including records outside the current search or page. Foreign-key cascades remove their
turns and documents. A native destructive confirmation explicitly describes this scope;
active chats remain untouched. Repeating the request on an empty trash is harmless.

The sidebar keeps native source-list selection, compact two-line rows, clear search and
empty/loading states. Context menus and the active chat's action menu expose rename,
fork and trash. Rename has a focused sheet with a 128-character limit. The active title
and action menu survive a sidebar filter that hides the active conversation.

Light, dark and system appearance are persisted in app preferences and selected from
the menubar. Shared window surfaces, text, borders and accent colors adapt together;
existing open scenes observe changes. Dark remains the default. The notch's black
presence is kept as its dedicated visual treatment. No inference provider is changed.

## Validation

Regression coverage checks independent multi-page forks, document retention, encrypted
titles, source deletion, namespace isolation, all-page trash deletion, invalid/secret
payloads, capacity rollback and malformed native action responses. Compile and inspect
the installed interface in both appearance modes, including chat actions and trash.
