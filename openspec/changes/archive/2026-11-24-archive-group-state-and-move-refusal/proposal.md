# Proposal

## Why

Two items of issue #51 are still open. The ranking group list in AWS shows visible, hidden or frozen, but not whether a group is archived, so an operator has to open each group to find out. And a contest can be moved out of an archived group, or its group cleared, which leaves the contest's data in the archived group's public ranking with nothing to update or remove it.

## What Changes

- AWS: the ranking group list gets an "Archive" column that says, for each group, "archived since ...", "will be archived on ..." or "never archived". It is computed from `archive_at` when the page is built; nothing is stored.
- AWS: saving a contest that is in an archived group SHALL be refused, with a notification and nothing saved, when the save moves the contest to another group or clears its group. Saving it while it stays in the group is still allowed.
- Manual: the "Known limits" and the list description of the archiving section of `docs/multi-contest.md` (and its Spanish `.po`) say so.

Not built: refusing to delete a contest of an archived group (the documented order is archive the group, then remove its contests), a static snapshot or lazy loading in the ranking server, a stored archive column, a database constraint, a "group" column in the contest list.

## Capabilities

### New Capabilities

None.

### Modified Capabilities
- `ranking-group-archiving`: the requirement "AWS refuses changes that an archived group would swallow" also covers moving a contest out of an archived group; a new requirement covers the archive state in the group list.

## Impact

- `cms/server/admin/handlers/contest.py` (one check), `cms/server/admin/templates/ranking_groups.html` (one column), the AWS catalog `aws.pot` and `es/aws.po`.
- `docs/multi-contest.md` and `docs/locale/es/LC_MESSAGES/multi-contest.po`.
- No schema, RPC, ProxyService or ranking server change. Not deployed by merging.
