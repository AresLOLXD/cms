# Tasks

Each numbered group gets its own Superpowers plan
(`openspec/changes/archive-ranking-groups/plans/YYYY-MM-DD-<group>.md`) and is
reviewed alone. The groups run in order: 1, then 2 to 5.

## 1. Data model and migration

- [x] 1.1 Add `RankingGroup.archive_at` and `is_archived_at(now)`, with tests for the three "Archived from archive_at" scenarios (`cmstestsuite/unit_tests/db/`)
- [x] 1.2 Add `cmscontrib/updaters/fork_ranking_group_archive.py`, call it from `cmscontrib/SetupDB.py`, and extend `schema_diff_test` and `SetupDBTest`, with a test for "Updating an existing database" (existing groups stay empty)

## 2. The default archive_at

- [x] 2.1 Add the computation of a group's last contest end (latest `stop` of the participation groups of its contests) plus the 24 hour constant, with tests for "The last contest decides" and "A group with no contests"
- [x] 2.2 Make the sweep fill an empty `archive_at` only when the instant is in the future, with its own session commit and a log-and-retry on a database error, with tests for "A group with a contest that has not ended gets the default", "A set archive_at is kept" and "Contests that already ended do not archive a group by surprise"

## 3. ProxyService stops sending to archived groups

- [x] 3.1 Make `_is_sent` and the event paths skip archived groups, with tests for the score, token and dataset scenarios and for "A contest added to an archived group"
- [x] 3.2 Make the sweep and `initialize` skip archived groups, with tests for the sweep scenario and the "other groups still get their data" case
- [x] 3.3 Keep archived groups in the group mapping used by `_reinitialize_sync`, with tests that reinitialize sends no reset, delete or put to an archived namespace and that unarchiving resumes the sending
- [ ] 3.4 Safety net: `_regenerate_ranking_sync` returns before its reset for an archived group (silently; group 5 adds the WARNING and its scenarios), with a test that no reset is enqueued

## 4. The final visibility

- [ ] 4.1 Send one visibility with no windows when a group is found archived, remember it in memory, and forget it on unarchive, with tests for the hidden and frozen scenarios, "The final visibility is sent once" and "Unarchiving forgets the final visibility"

## 5. Regenerate

- [ ] 5.1 Make the early return of `_regenerate_ranking_sync` for an archived group (added in 3.4) log a WARNING naming the group and `archive_at`, with tests for both Regenerate scenarios

## 6. Verify

- [ ] 6.1 Run `openspec validate archive-ranking-groups`, the ProxyService group tests, the db tests, `schema_diff_test` and `pyflakes` (see CLAUDE.md "Testing" and "Linting"), then the Scenario <-> test check for `ranking-group-archiving`
