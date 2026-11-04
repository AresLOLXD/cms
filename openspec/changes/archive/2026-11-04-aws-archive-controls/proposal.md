## Why

The core of archiving ranking groups is in place (change `archive-ranking-groups`): a group has an `archive_at`, ProxyService stops sending to an archived group, and the field is filled with a default. But the operator can only read or move `archive_at` with SQL, nothing in AWS says that a group is archived, and AWS still lets a contest be assigned to an archived group or the group be renamed, which silently sends nothing or resets its public namespace. Issue #51 asks for the AWS side so the operator can run the grace period and publish late corrections without touching the database.

## What Changes

- The ranking group page in AWS shows the group's archive state and `archive_at` (UTC and local time) and lets the operator edit `archive_at`, press **Archive now** and press **Unarchive**.
- Any save that takes a group from archived to not archived (the **Unarchive** button, or an edit that moves `archive_at` to the future or clears it) asks ProxyService to regenerate that group once, so corrections made while it was archived reach the public ranking. **Unarchive** sets `archive_at` to now plus 24 hours, so the group archives itself again.
- AWS refuses, with a notification and nothing saved, to assign a contest to an archived group and to rename an archived group.
- Every AWS page of a contest whose ranking group is archived shows a banner saying that changes will not reach the public ranking until the group is unarchived, with a link to the group page.
- The AWS **Regenerate** button on an archived group says that it did nothing, instead of "requested".
- Docs (`docs/multi-contest.md` and the runbook in `docs/contest-day.md`, en and es `.po`).
- Out of scope, stays open in #51: the static snapshot, a state column in the group list, refusing to move a contest out of an archived group.

## Capabilities

### New Capabilities

### Modified Capabilities
- `ranking-group-archiving`: adds the AWS behavior (state, editing, archive now, unarchive with regenerate, refusals, banner, regenerate notification) to the requirements that so far cover only ProxyService and the data model.

## Impact

- `cms/server/admin/handlers/rankinggroup.py`, `handlers/contest.py`, `handlers/base.py` (banner data) and the templates `ranking_group.html`, `base.html`.
- `ARCHIVE_GRACE` moves from `cms/service/ProxyService.py` to `cms/db/rankinggroup.py` so AWS can use it without importing the service; ProxyService keeps the name.
- No schema change, no change to ProxyService behavior, no new dependency.
- Tests under `cmstestsuite/unit_tests/server/admin/`.
