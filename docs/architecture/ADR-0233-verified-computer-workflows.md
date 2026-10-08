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
loopback CDP connections. Advanced imported profiles retain an exact reviewed HTTPS tab URL
and origin list. Normal Chrome/Edge sessions use the official Playwright extension and its
connection consent; the current task selects the page after its own approval.
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

Configuration → Accounts and integrations → Connect browser stores only enabled/mode defaults.
No URL, tab index, origin, remote-content consent or reusable grant is stored by this normal
flow. Advanced imported profiles remain available through the existing MCP settings.
Install peer dependencies outside the app bundle with
`script/install_mcp_peers.py`; installation uses the committed lockfile and disables npm
lifecycle scripts. No runtime `npx`, browser binary, or Node executable is embedded in Jarvis.

## Access within a browser task

`browser_task_open` asks the broker to approve the exact destination, original task and NVIDIA
processing. If the request contains explicit URLs, the proposed destination must be one of
them. A browser-control request naming a site can instead propose a public HTTPS URL for the
owner to review. The pinned peer and its eight reviewed input contracts are admitted before
any browser tool runs. A unique exact URL match is selected automatically; no match opens a
new page. Multiple matches stop without selecting a page or reading its content.

The observation is bound to a daemon-owned request ID, runtime session nonce, exact URL and
snapshot digest. Access evidence certifies only the connection. `browser_task_action` reuses
the approved site's scope for bounded operations; it cannot obtain another site's access,
submit a form, handle credentials, transfer files or execute arbitrary code. Access to a new
origin requires another exact broker decision. If the original instruction included URLs,
additional destinations must also occur in that instruction; another destination needs a
new request. Preference revocation, expiry, cancellation,
uncertain effects and task completion disconnect the peer. This never closes a personal tab.

The existing LangGraph execution loop replans from verified observations under the same
request, with at most 24 actions and ten minutes of session lifetime (the job budget also
applies). Browser grants and executable sessions are not persisted in task recovery. A later
request must obtain its own access; page content, chat history and model output cannot supply
that authority.

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
`script/qualify_browser_tasks.py --run --packages PATH --node PATH` separately exercises
request-scoped approval, automatic unique-tab selection/new-tab creation, exact DOM effects,
duplicate-tab refusal, revocation and peer cleanup without closing browser tabs. Its injected
CDP transport belongs only to the private fixture. This does not exercise the interactive
extension connection consent or certify access to an authenticated personal browser.
Media playback requires an exact newly owned YouTube URL, playable video state and advancing
time before reporting playback. Unit evidence does not claim universal application support,
real-account browser certification, or the deferred physical voice P10/P11 certification.
