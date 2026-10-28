# Design

## Context

Observed in the code (not assumed):

- `RankingGroup` (`cms/db/rankinggroup.py`) holds `hide_at`/`show_at` and
  `freeze_at`/`unfreeze_at`, naive UTC, nullable, with `window_is_open`
  helpers. These only change what RWS *shows*, not what is sent. RWS applies
  those windows with its own clock.
- ProxyService decides what to send in two places:
  - `_is_sent(contest)`: the gate of the event paths
    (`_submission_scored_sync`, `_submission_tokened_sync`,
    `_dataset_updated_sync`).
  - `_contests_to_send(session)`: used by the sweep
    (`_missing_operations_sync`), `initialize`, `_reinitialize_sync`,
    `_regenerate_ranking_sync`, and by `_compute_group_contests`.
- `_enqueue_visibility` sends every group's visibility at every sweep.
- `_reinitialize_sync` computes `lost` groups by comparing the previous
  `_group_contests` with a mapping built from `_contests_to_send`, and
  enqueues a `RESET` for each lost group.
- `_regenerate_ranking_sync(group)` enqueues a `RESET`, the group's
  visibility, its contests and its submissions.
- A contest ends, for a participant, at the `stop` of the participation
  `Group` that participant belongs to; a contest has a main group and may have
  others, so the end of a contest is the latest `stop` of its groups.
- Existing databases are updated by `fork_*.py` modules called from
  `cmscontrib/SetupDB.py`, with `schema_diff_test` checking they match what
  `cmsInitDB` creates (see `cmscontrib/updaters/fork_submission_limits.py`).
- ProxyService today only reads the database.

## Goals / Non-Goals

**Goals:**
- An archived group receives nothing from ProxyService (after one last
  visibility), and its sweeps skip it.
- The final standings stay in RWS untouched.
- Every group gets a visible default `archive_at` without any existing group
  being archived by surprise.
- Existing databases get the new column on the next `cmsSetupDB`.

**Non-Goals:**
- AWS UI (view or move `archive_at`, archive now, unarchive), and rejecting a
  contest assigned to an archived group.
- The warning when an admin action affects an archived group.
- A configurable delay, RWS dropping archived groups from memory, or a static
  snapshot.
- Docs and runbook.

## Owner's decisions (2026-10-28, answers to the former open questions)

- **Default and delay:** the default `archive_at` is the end of the group's
  last contest plus 24 hours, left in the `archive_at` field so it can be
  seen ("dejalo avisado en el campo").
- **Hide and freeze:** "se muestra el final y se archiva esa versión": the
  final standings are shown and that version is what is archived.
- **A contest added to an archived group:** nothing is sent until the group
  is unarchived.
- **Regenerate on an archived group:** it does nothing, and says so.

## Decisions

1. **One nullable column, `RankingGroup.archive_at`** (naive UTC, like the
   other windows), plus `is_archived_at(now)`: `archive_at is not None and
   archive_at <= now`. No stored "archived" flag: the state derives from the
   clock, so nothing has to flip it. Alternative: a boolean plus a date;
   rejected because it can disagree with itself and needs a job to set it.
2. **Skip at the existing gates.** `_is_sent` returns False for a contest of
   an archived group, and the sweep, initialize and regenerate paths skip
   archived groups. This also covers a contest assigned to an archived group.
3. **Do not filter the group mapping.** `_compute_group_contests` must keep
   counting the contests of archived groups, otherwise `_reinitialize_sync`
   sees the group as `lost` and resets its namespace, wiping the final
   standings. Either it reads contests without the archived filter, or
   `_contests_to_send` takes a flag. Decided in the plan; the spec scenario
   "Initialize and reinitialize leave an archived group alone" covers it.
4. **Clock.** ProxyService reads `datetime.utcnow()` where the group is
   evaluated, once per sweep or event, consistent with how
   `is_hidden_at(now)` is used elsewhere. Exact call site: to confirm when
   planning.
5. **Migration.** A new `fork_ranking_group_archive.py` guarded by an
   `information_schema` lookup, same pattern as `fork_submission_limits.py`.
   It adds the column empty and never fills it.
6. **Default `archive_at` (author's call).** The end of a group's last contest
   is the latest `stop` of all the participation groups of all its contests
   (analysis mode is not counted); the grace period is a constant of 24
   hours, not an option. ProxyService fills the field in its sweep, in the
   same pass that already reads the groups, when ALL of these hold: the field
   is empty, the group has at least one contest, and the computed instant is
   in the future at that moment. Reasons: one place, no AWS code (the owner
   kept the AWS UI out of this slice); a group whose contests already ended
   before this ships, such as one that finished before the deploy, keeps an
   empty field and is never archived by surprise, until the operator sets it.
   It never overwrites a value that is set. This is the first column
   ProxyService writes, so it needs its own session commit and a test that a
   database error there does not stop the sweep (it logs and tries again at
   the next sweep). Alternative rejected for this slice: filling it in the AWS
   save handlers (three handlers, AWS code, and the field would be stale if
   an import changes the times).
7. **Final visibility (author's call; confirm).** The first time a sweep or
   an initialize finds a group archived, ProxyService enqueues ONE visibility
   operation for it with every window empty (shown, not frozen) and the
   stored staff password, and remembers in memory that the group's final
   visibility was sent. Nothing else is sent to the group afterwards.
   Unarchiving (moving `archive_at` to the future or clearing it) forgets
   that mark, so the normal visibility of each sweep resumes. The mark is not
   persisted: after a ProxyService restart the same operation is sent once
   more, which changes nothing in RWS.
8. **Regenerate.** `_regenerate_ranking_sync(group)` returns before building
   anything when the group is archived: no reset, no data, and a WARNING that
   names the group and `archive_at` and says to move `archive_at` to the
   future to publish a correction. (The RPC returns normally; the AWS page
   still says "requested".)

## Risks / Trade-offs

- **Reset of an archived group** (Decision 3). The highest risk: one
  careless filter in `_contests_to_send` destroys a final ranking at the next
  reinitialize. Needs its own test.
- **The final visibility reveals a hidden group** (Decision 7). A group that
  was hidden on purpose, with a staff password, becomes public when its
  `archive_at` arrives. It follows the owner's answer; to be confirmed. The
  way to keep a group hidden is to leave `archive_at` empty or move it far
  into the future.
- **A stale default after a contest is extended** (Decision 6). The default
  is stored once. If an admin later moves a contest's `stop` past
  `archive_at`, the group is archived while that contest is still running:
  ProxyService stops sending it. Mitigations are for the AWS change (move
  `archive_at` when the times are saved, show it next to the times); until
  then an operator who extends a contest must move `archive_at` by hand.
- **Archive is not a lock on the data.** The ranking keeps what it had; the
  database can still change. Until the AWS warning change exists, an
  operator can change scores in AWS and the public ranking will silently not
  follow. Accepted for this slice, called out in the proposal.
- **A late sweep after archive_at.** An operation already in the ProxyService
  queue when the group becomes archived is still sent. Accepted; to confirm
  in the plan.
- **Unarchiving needs a Regenerate.** Scores and tokens that changed while
  the group was archived are in `scores_sent_to_rankings` as sent (the event
  paths skip without marking them), so after `archive_at` moves to the future
  the normal sweep does not resend them. Pressing Regenerate for the group
  does. The AWS change is meant to do that on unarchive.
- **No UI yet.** Moving `archive_at` needs SQL until the AWS change exists.

## Open Questions

None for this slice. Items to confirm are marked "author's call" above
(Decisions 6 and 7).
