## 1. Group page: state, archive_at, archive now, unarchive

- [x] 1.1 Move `ARCHIVE_GRACE` to `cms/db/rankinggroup.py` and import it in ProxyService; test that `ProxyService.ARCHIVE_GRACE` is still 24 h
- [x] 1.2 Add the `archive_at` field to the group page (render in UTC with local time, read with the `_shown` rule, empty clears, malformed rejects); tests for the Scenarios of "AWS shows the archive state" and "The operator can move archive_at"
- [x] 1.3 Add the `archive_now` and `unarchive` actions with their stale-page errors (the Unarchive button asks for confirmation, since it regenerates); tests for the Scenarios of "Archive now" and the button Scenarios of "Unarchiving publishes the corrections"
- [x] 1.4 Schedule one `regenerate_ranking` after the commit when a save takes the group from archived to not archived; tests for the edit, stay-archived and stay-not-archived Scenarios

## 2. Refusals

- [ ] 2.1 Refuse renaming an archived group and refuse assigning a contest to an archived group (not when it stays in its own archived group); tests for the four Scenarios of "AWS refuses changes that an archived group would swallow"

## 3. Banner and regenerate notification

- [ ] 3.1 Pass the archived group of the page's contest to the templates and render the banner in `base.html`; tests for the three banner Scenarios
- [ ] 3.2 Make the AWS Regenerate handler say that an archived group was not regenerated, and skip the RPC; tests for the two Scenarios of "AWS says when a regenerate does nothing"

## 4. Docs

- [ ] 4.1 Document the page controls, the 24 h unarchive, the regenerate on unarchive, the refusals and the banner in `docs/multi-contest.md` and the contest-day runbook in `docs/contest-day.md`, with the Spanish `.po` and the docs check passing
