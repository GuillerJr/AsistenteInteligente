# Verified computer and browser workflows

Date: 2026-10-08. Supersedes completion by label/title coincidence in the visual driver.

Jarvis retains its native Accessibility/CoreGraphics helper and the reviewed Playwright MCP peer.
No second orchestration engine, unrestricted shell executor, local language model, or browser
profile copy is introduced.

## Native action and objective verification

`computer_use` accepts up to six explicit postconditions. Literal values and targets must be
present in its approved objective. Exact field values, selected/expanded/enabled control state,
window titles, focus, and URLs are checked against bounded Accessibility metadata. Screenshot
pixels and a model's `done` response cannot certify completion. Generic goals without an
observable condition stop before dispatch. A literal gesture can certify its native delivery;
its report identifies that narrower verification scope.

Reports retain bounded checks, last action, failure category, and observation hashes. Content and
typed text are not added to diagnostic metadata. An uncertain effect after a mutation produces
partial progress; it is never classified as a stale pre-action observation eligible for replay.
Recovery reload is restricted to supported browsers. Reflection must preserve the result and
verify the requested conditions, rather than checking only that no dialog remains.

Every text fragment and event boundary rechecks hardware input, process lifetime, focused
window and target. An event already pressed is released during cleanup even after takeover.
An attempted Accessibility mutation never falls through to a duplicate visual action. If
AXPress is unavailable, a transient 120×120 crop can go to the configured NVIDIA vision API.
The resulting correction still must resolve to the original, non-sensitive AX element and
window. The local handler has a six-second budget; the model never authorizes an action.

## Browser sessions

The existing pinned peer (`@playwright/mcp` 0.0.80) supports isolated, extension, and explicit
loopback CDP connections. Attachment requires an exact reviewed HTTPS tab URL and origin list.
Normal Chrome/Edge sessions use the official Playwright extension and its owner tab selection.
Credentials and profiles are not copied. Each action carries a runtime session nonce, expected
URL, and digest of the preceding bound snapshot. The adapter serializes calls and rechecks
tab/content before dispatch and after the effect; revocation invalidates the runtime session.

Eight tools are admitted for a session: snapshot, navigate, click, fill, select option, key,
tabs, and bounded wait. References come from the current snapshot. No arbitrary code, selectors,
storage/cookie access, file-transfer API, or attached-browser close is exposed. Evidence states
whether the action was performed or its explicit observable condition was met. Other tabs'
titles/content and peer logs are excluded from returned observations.

This is not an OS sandbox or network egress boundary: a live site or human can race the last
check, and a click can cause a site-side effect that cannot be rolled back. Such results stop
without claiming completion. Origins limit Jarvis's admitted navigation, not every request a
web page may initiate.

Configuration → Accounts and integrations → Connect browser prepares a disabled reviewed
profile. It discovers schemas only, calls no browser tool, preserves existing integrations, and
requires owner consent to send site content to NVIDIA. Activation and applying settings use
the existing settings flow. Install peer dependencies outside the app bundle with
`script/install_mcp_peers.py`; installation uses the committed lockfile and disables npm
lifecycle scripts. No runtime `npx`, browser binary, or Node executable is embedded in Jarvis.

## Ordered tasks and interruption

Up to four application steps and 24 total actions reuse the existing LangGraph/job execution
path. Each step has its own exact broker approval. Only the current step is executable; later
steps remain declarative. The encrypted interruption journal stores progress and checks, never
a reusable grant. An interrupted executing step, unverified effect, cancellation or takeover
prevents automatic replay. Continuing a pending step requires a new request and approval.

## Qualification boundaries

Deterministic tests cover false completion, missing/ambiguous conditions, partial mutation,
takeover, stale tabs/digests, revoked sessions, schema changes, and interrupted ordered tasks.
`script/qualify_computer_workflows.py --run --packages PATH --node PATH` exercises the actual
peer, broker and disposable Chrome against a synthetic HTTPS fixture with independent DOM
oracles. Its browser profile, certificate, pages and server are temporary; a proxy denies
external navigation. P8/P9 retain their narrower native/DOM qualifications. Direct P9 also
creates and disposes its own CDP context/page, so it cannot replace a personal tab's document.
Media playback requires an exact newly owned YouTube URL, playable video state and advancing
time before reporting playback. Unit evidence does not claim universal application support,
real-account browser certification, or the deferred physical voice P10/P11 certification.
