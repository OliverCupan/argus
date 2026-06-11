---
date: 2026-06-11T00:00:00
researcher: GIVERNY
git_commit: b5575b4+working-tree
branch: master
topic: "Find the problem — /api/compact 404 during verification"
status: complete
---

# Research: Compact Endpoint 404 Root Cause

## Summary
The `/api/compact` endpoint is correctly implemented and works on a freshly started server. The 404 during verification was caused by a **stale server process** started before the implementation subagents wrote their changes. All features are confirmed working end-to-end on a fresh server.

## Root Cause
First verification attempt started `python gui.py` (PID 7481) and then used the cached process. That process loaded module bytecode from before the Phase 2 changes were written. Re-starting on port 7778 yielded a fresh module load with all changes present.

## Confirmed Working (fresh server on port 7778)

| Check | Result |
|---|---|
| `POST /api/compact` HTTP status | **200 OK** |
| Response body | `{"ok": true, "message": "Compact requested"}` |
| `/api/compact` in OpenAPI spec | **Yes** (`/openapi.json`) |
| `GET /api/compact` | 405 Method Not Allowed (correct) |
| `compact-btn` in served HTML | **Present** |
| `_buildCompactionRow` in served JS | **Present** (2 occurrences) |
| `_showToast` in served JS | **Present** (3 occurrences) |
| WS compaction event after POST | `event_type: compaction` with `data: {kind: "manual_requested", messages_dropped: 0, tokens_saved_est: 0}` |

## WS Event Flow Verified
```
connect  → drain: connections_update
connect  → drain: token_update (initial stats)
POST /api/compact → event_type=compaction, data={kind: manual_requested, ...}
```
Event correctly flows through EventBus → broadcast loop → WS clients.

## Remaining Manual-Only Checks
These require a browser (cannot automate without Playwright):
- Visual appearance of purple badge in Activity Log timeline
- Toast notification popup (2s fade)
- "⚡ Compact" button appearance in topbar
- Badge rendering during an actual agent run with large tool output

## Open Questions
None — implementation is correct. Visual checks require human eye.
