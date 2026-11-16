## Why

After the machinery (change A) and the pages that were Spanish-only (change B), only the login, the error page, the switches and four pages of the admin site (AWS) follow the language chosen for the request. About 1,070 more texts, across 70 templates, the handlers that raise notifications and form errors, and the scripts, are still English only. An admin who picks Spanish gets a half-translated site. This change finishes issue #30 for AWS: every admin page follows the language, and a guard makes sure it stays that way.

## What Changes

- Every page group of AWS (chrome and sidebar, contest, task, dataset, submissions and user tests, users and teams and groups, participations and activity and ranking, system and admins) shows its texts in the language of the request, with Spanish translations in the `aws` catalog.
- Buttons that send an operation to the server keep sending the same value whatever the language; only their label is translated.
- Notifications and form errors that the handlers raise use the language of the admin who triggered them.
- Script texts (the shared admin script, the overview, resources and task page scripts, confirmations) come from the server in the page language.
- Evaluation outcomes, sizes and `N/A` shown in the admin pages use the contestant-site Spanish when AWS has no text of its own.
- The quick-answer labels of the question form are translated; the stored answers stay English.
- The Spanish catalog follows one style guide (register, glossary, punctuation), and a check enforces the mechanical rules.
- Guards: a test fails when a template, script or handler adds text without gettext; the English pages stay identical while the work is in progress.
- Out of scope, stated as "What we will not build" below.

## What we will not build

1. No change to any English text: no «occured» fix, no «Remove» to «Delete» rename, no `str(error)` in place of `repr(error)`.
2. No translation of `cms/grading` names and parameters: task and score type names, parameter names, descriptions and choices, score-type parameter help, language names.
3. No translation of stored or exported data: quick-answer values and `reply_subject`, announcement and message bodies, `ranking.txt`, activity CSV headers, database validation detail, library exception text.
4. No translation of HTTP error reasons, the critical-error fallback text or log messages.
5. No per-admin language for notifications: the global queue and the actor-language text stay.
6. No `pgettext` machinery.
7. No change to the contestant-site catalog or to the contestant site, no ranking wording, no new languages.
8. No JavaScript i18n library, no JavaScript plural engine, no refactor of the existing import and export page scripts.
9. No CSS or visual change beyond turning the operation `<input type=submit>` into `<button>`.
10. No permanent English snapshot, no committed catalog-merge script.

## Capabilities

### New Capabilities

### Modified Capabilities
- `aws-language`: every admin page follows the language; operation buttons post language-independent values; notifications and errors follow the admin's language; script texts and evaluation outcomes follow the language; quick-answer labels; the Spanish catalog follows a style guide.

## Impact

- `cms/server/admin/`: about 70 templates, 20 handlers, `static/aws_utils.js`, `formatting.py`, `server.py`, `handlers/base.py`.
- `cms/locale/aws.pot` and `cms/locale/es/LC_MESSAGES/aws.po` grow by about 1,000 messages; `cms/locale/_fragments/` exists only on lane branches.
- New tests: the guard module and its helper, the catalog fragment and style checks, the fallback test, one `translated_<lane>_test.py` per lane.
- No database change, no new dependency.
