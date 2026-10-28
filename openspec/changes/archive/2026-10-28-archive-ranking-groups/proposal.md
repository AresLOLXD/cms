# Proposal

## Why

A ranking group keeps receiving updates from ProxyService for as long as CMS
runs: every sweep (about every 6 minutes) and every score, token or dataset
event in its contests. Once a contest is over its public ranking should stay
fixed, but today an AWS edit, a re-evaluation or a Regenerate still reaches
it, and every old group keeps costing ProxyService and RWS work on every
sweep (issue #51, requested by the operator on 2026-10-08).

Corrections still happen the same day a contest ends, so a group cannot be
frozen at the moment its contest ends: it needs a grace period.

## What Changes

This is the core slice of issue #51. It is the data model and ProxyService
behavior only. The owner's decisions on the open questions are in
`design.md` ("Owner's decisions").

- A ranking group gets an `archive_at` instant (naive UTC, nullable). Once it
  is in the past the group is archived. Until then nothing changes.
- `archive_at` gets a default: the end of the group's last contest plus 24
  hours, stored in the field so the operator can see it. ProxyService fills it
  when it is empty, only if that instant is still in the future, so no
  existing group is archived by surprise. A group with no contests keeps it
  empty (never archived).
- ProxyService stops sending an archived group scores, tokens, contest data
  and, after one last send, visibility. The sweep skips it and so do the
  score, token and dataset events. A contest assigned to an archived group
  sends nothing until the group is unarchived.
- When a group becomes archived, ProxyService sends its visibility once as
  shown and not frozen, so its final standings are what stays public (a group
  that was hidden on purpose becomes visible; see the risks in `design.md`).
- Regenerate on an archived group does nothing and logs a WARNING.
- RWS keeps serving the archived group's last standings read-only. No RWS
  change is expected.
- A database update adds the `archive_at` column to existing databases, with
  every existing group left empty (never archived).

Out of scope (separate changes): the AWS page to see or move `archive_at`,
archive now and unarchive; the warning when an admin action affects an
archived group; refusing a contest assigned to an archived group in AWS; a
configurable delay; a static snapshot (IOI `cms-ranking-archiver`); the docs
and runbook (en and es `.po`).

## Capabilities

### New Capabilities
- `ranking-group-archiving`: when a ranking group becomes archived, its
  default `archive_at`, what ProxyService sends, and does not send, to an
  archived group, and what its Regenerate does.

### Modified Capabilities
<!-- None: openspec/specs/ is empty, so there is no existing capability to modify. -->

## Impact

- `cms/db/rankinggroup.py`: new column and an `is_archived_at(now)` method.
- `cmscontrib/updaters/` and `cmscontrib/SetupDB.py`: a `fork_*.py` update that
  adds the column, with `schema_diff_test` checking it matches `cmsInitDB`.
- `cms/service/ProxyService.py`: `_is_sent`, `_contests_to_send`,
  `_enqueue_visibility`, the group mapping used by `_reinitialize_sync`, the
  sweep (fills `archive_at`, the first column ProxyService writes to the
  database), the one-time final visibility, and `_regenerate_ranking_sync`.
- Unit tests in `cmstestsuite/unit_tests/service/proxyservice_groups_test.py`
  and `cmstestsuite/unit_tests/db/`.
- No AWS, CWS or RWS code in this change. Not deployed by this change.
