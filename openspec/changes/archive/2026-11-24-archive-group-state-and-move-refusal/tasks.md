# Tasks

## 1. The archive column

- [x] 1.1 An "Archive" column in `ranking_groups.html` showing `archive_summary`; the "Archive" header is already in the catalogs (check the guards); a render test of the list rows covers the three Scenarios "An archived group in the list", "A group that will be archived in the list", "A group with no archive_at in the list"

## 2. The move refusal

- [x] 2.1 In `ContestHandler._post_sync`, refuse a move out of an archived group (another group or none) with a translated message, and `# shortcut:` for the missing row lock; tests in `contest_test.py` cover Scenarios "Moving a contest out of an archived group", "Clearing the group of a contest in an archived group", "Moving a contest between groups that are not archived", and keep "Editing a contest of an archived group" green
- [x] 2.2 The new message in `aws.pot` and `es/aws.po`, regenerated with the documented commands; the AWS i18n guards pass

## 3. Docs and verification

- [x] 3.1 `docs/multi-contest.md`: the archiving section says the list shows the state and a contest cannot leave an archived group from AWS (the "Known limits" are updated), with the Spanish `.po`; `docs/check_translations.py` and the docs builds pass
- [x] 3.2 Verification: the asyncio pytest group of the admin server, locale and server, pyflakes, `openspec validate archive-group-state-and-move-refusal --strict`, the Scenario to test check

## Workflow follow-up

- Archive the change after the review and verification, as its own commit.
