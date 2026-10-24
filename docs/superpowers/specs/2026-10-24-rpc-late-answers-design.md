# RPC clients: late answers to abandoned calls at DEBUG (issue #67) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (python-pro); the owner approved the
consolidated design.

## Facts
- `cms/io/async_rpc.py`: `execute_rpc` drops the pending request when the
  caller gives up; `process_incoming_response` then logs `No pending request
  with id … found.` at WARNING for each late answer (~300 at once when
  ScoringService recovers after a 5-minute stall).
- Ids are `uuid.uuid4().hex`, not a counter (no "below the counter" trick).
- The gevent client (`cms/io/rpc.py`) cannot hit this: no caller has a
  cancel/timeout path, so a late answer always finds its entry. Leave it.
- Every give-up site already logs the timeout (`schedule_rpc`,
  `workerpool._fire_and_forget`, ScoringService's `NotificationFailureLatch`
  WARNING + INFO "can notify … again: N dropped"), so the late answer says
  nothing new.

## Non-goals
A gevent mirror, a new id scheme, counters/metrics, a config knob for the cap,
a summary WARNING, clearing the structure in `finalize()` (the cap bounds it).

## Decisions (`AsyncRemoteServiceClient` only)
1. `MAX_ABANDONED = 4096` class attribute; `self._abandoned:
   OrderedDict[str, str]` (id → method) in `__init__`.
2. In the `finally` of `execute_rpc`: `request =
   self.pending_outgoing_requests.pop(id_, None)`; if not None (no answer and
   no `finalize()` took it, so the caller gave up) record
   `self._abandoned[id_] = method`, and `popitem(last=False)` beyond the cap.
3. In `process_incoming_response`, when the id is not pending:
   `method = self._abandoned.pop(id_, None)`; if found, log DEBUG ("late
   answer to %s, the caller gave up"); otherwise keep the WARNING (ids never
   sent, or a duplicate answer).
4. `AsyncFakeRemoteServiceClient` needs nothing.
Risk to note: a late ERROR answer is no longer logged above DEBUG, and the
peer does not log it either (`process_incoming_request` only puts the
traceback in the response); its first line is appended to the DEBUG line.
A call is recorded only once its request was written (`sent`): the ones that
fail before that ("Not connected.", "Message too long.") would otherwise fill
the cap during an outage.

## Tests (`cmstestsuite/unit_tests/io/async_rpc_test.py`, next to
`test_cancelled_call_leaves_no_pending_entries` in `TestExecuteRpcCleanup`)
1. Cancel a call against `StuckPeer` (`cmstestsuite/unit_tests/stuckpeer.py`;
   add a tiny helper if it cannot answer a chosen id late), then answer it
   late → no WARNING, one DEBUG; an unknown id → WARNING; a second answer
   with the already-used id → WARNING.
2. Patch `MAX_ABANDONED` to 2, cancel 3 calls → answering the first warns, the
   last does not.

## Docs (en + es .po by hand)
The contest-day runbook row about "No pending request" (find it in
`docs/contest-day.md`; add one if missing): many such lines at DEBUG mean a
stuck peer answered late; a WARNING means an answer to an id never sent or
answered twice. `cmstestsuite/loadtest/analyze.py` greps that text: check it
still works (DEBUG lines may not be in its logs; adjust only if needed).

## Tasks
1. Code + tests. 2. Docs + docs check.
