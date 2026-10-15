# Ranking Data Refused in a Batch Is Not Lost — Design Doc

**Date:** 2026-10-07
**Issue:** #13, item S1 (wave 1 of `2026-10-06-post-10-10-roadmap-design.md`)
**Status:** Designed and approved autonomously. The user asked for the
backlog to advance without stopping, always through brainstorming.
Decisions marked *(autonomous)* took the recommended option and can be
revisited. Target: `beta`, after the 2026-10-10 contest. That contest is
covered by operating rules instead (see Out of Scope).

## Problem

The spike of 2026-10-07 confirmed S1. Its tests are in `733c83b2`, which
this branch carries.

- **RWS stores a list all or nothing.** `Store.merge_list`
  (`cmsranking/Store.py:227-244`) checks every entity before it stores any.
  If one submission names a user or task RWS does not know, or one
  subchange names an unknown submission, the whole PUT gets a 400
  (`cmsranking/RankingWebServer.py:209-215`).
- **ProxyService drops the whole batch.** In each round it merges the data
  of every queued operation into one PUT per namespace and entity type
  (`cms/service/ProxyService.py:604-612`). It treats any 4xx as final
  (`_send`, 695-715) and does not queue the refused entries again.
- **The lost data is never sent again.** A score or token counts as sent
  when its operation is built (`scores_sent_to_rankings.add`, 1126; tokens
  at 1158), so the sweeper skips it. The sweeper sends contest data
  (contest, tasks, teams, users) again only for broken contests (947-952).
  Only Regenerate, a ProxyService restart, or a new score of the same
  submission brings the data back.
- **The trigger** is a user or task that exists in CMS but never reached
  RWS. The usual way that happens is a participation added during a
  contest through the CLI or CMS-Loader instead of AWS. Every round that
  carries one of that contestant's submissions then loses the valid
  scores of everyone else in the same round, and the public ranking shows
  them as missing.

## Goal

1. A refused batch loses only the entities that RWS refuses on their own.
2. A score or token refused because RWS lacks its user or task is sent
   again once that dependency is there. That must happen within one
   sweep, without Regenerate.
3. When nothing is refused, ProxyService sends the same requests as today.

## Approaches Considered

- **A (chosen): split a refused batch, and repair the contest data of a
  namespace that refused something.** *(autonomous)*
  - **Split.** When RWS refuses a merged PUT of more than one entity,
    ProxyService sends that type's entities again one by one. Only the
    entities RWS still refuses alone are dropped.
  - **Repair.** The submissions behind refused scores and tokens are taken
    out of the "sent" sets. The contests of that namespace are marked, and
    the next sweep sends their contest data and then those scores again.
  - **Cost.** Changes are confined to ProxyService. Extra requests happen
    only on a refusal.
- **B (rejected): RWS applies the valid entities and reports the invalid
  ones** (a partial-success answer).
  - It changes the RWS API and the Store, which cmsranking shares with its
    standalone users.
  - ProxyService would still need the repair path for the refused ones.
- **C (rejected): the sweeper sends every contest's data on every pass.**
  - It is simpler, but each pass sends every user, task and team of every
    ranked contest again.
  - RWS turns each of those into an update event for every connected
    scoreboard, every few minutes, all contest long.
  - The targeted repair of A sends contest data only for namespaces that
    refused something.

## Design

### 1. Split a refused batch (`ProxyExecutor._execute_sync` and `_send`)

- **Today.** `_send(group, type_, data)` returns SENT, REJECTED or UNSENT
  for the merged `data` of a type.
- **New rule.** When it returns REJECTED for a merged dict of more than one
  entity, and the type is a data type (contest, task, team, user,
  submission, subchange; never a reset or the visibility), send each
  entity again as a one-entry dict to the same list endpoint.
  - **Bisection was rejected:** refusals are rare, and one-by-one keeps the
    log and the tests simple. *(autonomous)*
  - **Outcome of each single send:**
    - SENT: the entity is in.
    - REJECTED: the entity is refused on its own and is dropped.
    - UNSENT: a communication or server error. Stop splitting. The
      entries not yet sent alone go back to the queue, and the group
      stalls, as an UNSENT batch does today.
  - **Which entries count as sent.** An entry, meaning one queued
    operation, whose entities all went in counts as sent. Its entities
    must not be sent again.
- **Ordering inside the round stays as it is.** Within a namespace, types
  go in the order contests, tasks, teams, users, submissions, subchanges.
  - A submission that is refused alone makes its subchange be refused
    alone as well (unknown submission).
  - Both are refused for the same missing dependency, and both are
    repaired by section 2.
- **One WARNING per refused type per round** replaces today's "rejected …
  use Regenerate" line. It names the namespace, the type, how many
  entities were refused out of how many, and up to 10 of their ids. The
  wording depends on the type:
  - **Submissions and subchanges:** "will be sent again after the contest
    data" (section 2).
  - **Every other type:** keep the advice to use Regenerate.
- **The executor's result.** `_execute_sync` returns, besides the unsent
  entries, the refused entities it could attribute: the submission ids of
  refused submissions, score subchanges and token subchanges, and the
  namespace of each refusal. `execute` (on the loop) hands them to the
  service (section 2).
- **Unchanged:** the visibility handling, the reset handling, the
  UnencodableError path (its data would fail the same way again) and the
  per-group backoff.

### 2. Repair the namespace (`ProxyService`)

- **Attributing refused entities to submission ids.**
  - A submission key is `"%d" % submission.id`.
  - A score subchange has `data["submission"]`, and its key ends in `s`.
  - A token subchange has `data["submission"]`, and its key ends in `t`.
- **On the event loop, for each refused entity:**
  - remove the submission id from `scores_sent_to_rankings` (a submission
    or score subchange) or from `tokens_sent_to_rankings` (a token
    subchange);
  - add every contest of that namespace to a new set,
    `_contests_to_repair`. In single-ranking mode the namespace is the
    root, which means every ranked contest.
- **The next sweep (`_missing_operations_sync`), for each contest in
  `_contests_to_repair`:**
  1. enqueue its contest data with `_enqueue_contest_data`, as for a broken
     contest. That sends a user or task RWS lacks;
  2. enqueue its submissions with `only_missing=True`, which now includes
     the un-marked ones;
  3. remove it from `_contests_to_repair`.

  The contest data and the scores go into the same queue, and a round
  sends contest data before submissions, so the dependency arrives first.
- **Thread safety.** The two sent-sets and `_contests_to_repair` are only
  touched on the event loop: by the score/token builders, which already
  run there or under the service's existing rules, and by the hand-off
  from `execute`. Check this in the plan against the current threads of
  `operations_for_score` and `_missing_operations_sync`, and keep the
  existing locking discipline.
- **When the repair cannot help.** A refusal that is not caused by a
  missing dependency (bad data) is refused again on each sweep. ProxyService
  takes it out of the sent set each time, so it is retried every sweep
  (about 6 minutes) with the same WARNING. That costs one more small PUT
  per sweep for each such entity, and the operator sees it until someone
  fixes the data and uses Regenerate. This was chosen over tracking which
  round is a repair round: it keeps the state machine simple, and the cost
  is bounded. *(autonomous)*

### 3. Logs and documentation

- **`docs/contest-day.md`,** where it describes the "rejected … use
  Regenerate" warning: scores refused this way now come back by
  themselves within one sweep. Regenerate is still the tool when the
  warning says so. Update the matching Spanish `.po` entry too.
- **No change to the AWS Overview.**

## Testing

- **Flip the spike test** `proxyservice_rws_rejection_test.py`:
  - Alice's valid score lands in the round where Bob's is refused;
  - Bob's score lands after the next sweep, once his user was sent with the
    contest data;
  - Regenerate is not needed.
- **Keep** the RWS test `cmsranking/test_put_list.py`: RWS stays all or
  nothing.
- **New ProxyExecutor tests:**
  - a refused merged batch is split and only the refused entities are
    dropped;
  - an UNSENT result while splitting puts the rest back in the queue and
    stalls the group;
  - a one-entity refusal is not split;
  - the visibility and reset types are never split;
  - the refused submission ids and namespaces come back from
    `_execute_sync`.
- **New ProxyService tests:**
  - refused ids leave the sent sets;
  - the sweep sends contest data, then the scores, for a repaired contest,
    once per sweep;
  - an entity refused again is retried on the following sweep, with the
    WARNING.
- **The existing ProxyService tests** (`ProxyServiceTest.py`,
  `ProxyService_test.py`, the reliability tests) pass unchanged, except
  where they assert the old "use Regenerate" wording for submissions.
- **No load test.** When nothing is refused, the requests are identical
  (Goal 3). One test asserts this: a round with no refusal sends exactly
  one PUT per type and namespace.

## Out of Scope

- **The 2026-10-10 contest.** It runs today's code under three operating
  rules: add late participants only through AWS; use Regenerate when the
  "rejected" warning appears; compare or Regenerate before unfreezing.
- **The rest of #13:** per-group heap, batch size cap, the shared send
  thread, the concurrent reinitialize, and one session per sweep.
- **RWS atomic writes and tolerant loading of damaged files.** These are a
  separate follow-up.
