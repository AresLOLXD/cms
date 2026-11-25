# Design

## Context

`ContestHandler._post_sync` (`cms/server/admin/handlers/contest.py`) is the only AWS path that sets a contest's ranking group. It already refuses a move into an archived group. The list handler already builds `visibility_view` for every group, which returns `archive_summary` (already translated); the list template ignores it. See proposal.md for the motivation.

## Goals / Non-Goals

**Goals:** show the archive state in the list; stop a contest leaving an archived group from AWS.

**Non-Goals:** see "Not built" in the proposal.

## Decisions

- **Own "Archive" column, computed in the template from `views[g.id].archive_summary`.** The State cell already holds the summary and a "next:" text. A stored flag would duplicate `archive_at <= now` and go stale, as nothing flips it at that instant.
- **The refusal sits next to the existing one**, in the same `try` of `_post_sync`, with the same "Invalid field(s)" path and one translated message. It compares the contest's current group with the new one (`None` included), so the check covers both a move and a clear. A contest in no group, or in a group that is not archived, is not affected.
- **Deleting a contest of an archived group stays allowed.** The documented order (archive the group, then remove its contests) needs it.
- **No lock.** The group state is read once, like the existing refusal. A move that commits just as the group becomes archived (Archive now, or `archive_at` passing on its own, which a row lock would not prevent) leaves the moved contest's data in the archived ranking. Regenerate refuses on an archived group, so the recovery is Unarchive (which regenerates) and then Archive now. `shortcut:` comment in the code.
- **Known limit: archived to archived.** Moving a contest from an archived group to another archived group shows only the "moving out" message; the second refusal would apply after the first is solved.

## Risks / Trade-offs

- [An operator now has to unarchive a group to move its contest] -> intended; the message says so. After the move the group can be archived again by Archive now.
- [A CLI importer can still change the group] -> out of scope; AWS is the only path the manual describes.
