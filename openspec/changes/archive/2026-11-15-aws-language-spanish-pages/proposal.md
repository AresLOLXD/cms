## Why

The pages that this fork added to the admin site (AWS) are written in Spanish only: the ranking group page, the bulk removal of users, the CSV import of users, and the export of contests. The rest of AWS is English, so one admin session mixes both languages, and an English-speaking admin cannot use these pages. The AWS translation machinery now exists (change A of issue #30); this change moves these pages and their messages to translatable text so they follow the language chosen on AWS.

## What Changes

- Every text of the ranking group pages, the bulk removal pages (users and contest users), the users CSV import (page, preview, errors, progress) and the contests export (page, errors, progress) becomes an English message with a Spanish translation in the AWS catalog. The Spanish an admin sees today stays word for word the same.
- Messages with counts use the right plural form in both languages.
- The notifications that these handlers raise, and the errors and statuses of the background import and export jobs, are shown in the language of the admin who started the action (a job keeps that language when another admin polls it).
- The JavaScript of the import and export pages gets its texts from the server, in the page language, instead of having Spanish inside the script.
- Out of scope: the other AWS pages (change C), a language stored per job, plural rules beyond Spanish and English, renaming the data keys that the scripts read, translating the CSV header aliases.

## Capabilities

### New Capabilities

### Modified Capabilities
- `aws-language`: adds the requirements for the pages that are Spanish today: they follow the page language, plurals, job language, script texts, and that the existing Spanish text and data keys do not change.

## Impact

- `cms/server/admin/`: `bulkremove.py`, `bulkimport.py`, `importjobs.py`, `exportjobs.py`, `handlers/bulkremove.py`, `handlers/contestuser.py`, `handlers/export.py`, `handlers/rankinggroup.py`, and the templates `ranking_group.html`, `ranking_groups.html`, `add_ranking_group.html`, `users_bulk_remove.html`, `users.html`, `contest_users.html`, `contest_users_import.html`, `export.html`.
- `cms/locale/aws.pot` and `cms/locale/es/LC_MESSAGES/aws.po` grow by a few hundred messages.
- Existing tests that read Spanish text (`bulkimport_test.py`, `contest_users_import_test.py`, `bulkremove_*_test.py`, `export_handlers_test.py`, `importjobs_test.py`, `rankinggroup_test.py`) assert the English message by default; a smaller set asserts the Spanish translation from the catalog.
- No database change, no new dependency.
