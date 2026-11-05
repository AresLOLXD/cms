## 1. Ranking groups

- [x] 1.1 `handlers/rankinggroup.py` (`visibility_view` and the window and archive labels with a gettext argument), `ranking_group.html`, `ranking_groups.html`, `add_ranking_group.html` (buttons, hint, tooltips, confirm texts) and the notifications of these handlers; Spanish pinned to today's text; tests for "A page in English", "A page in Spanish" and "Ranking group state in both languages"

## 2. Bulk removal

- [x] 2.1 `cms/server/admin/bulkremove.py`, `handlers/bulkremove.py`, `users_bulk_remove.html`, `users.html`, `contest_users.html`: msgids, plural forms, notifications in the request language; tests for "One and several", "Zero", "A notification" and the Spanish pin

## 3. Users import

- [x] 3.1 `bulkimport.py` (whole-sentence msgids per field, no `del` hack), `importjobs.py` and `handlers/contestuser.py` (gettext passed down, job texts in the starter's language, notices as `N_`); tests for "A job polled by another admin", "The import summary in English", "CSV aliases" and the Spanish pin
- [x] 3.2 `contest_users_import.html` and its script: template texts, `{% pluralize %}`, `data-msg-*` for the script; tests for "Script texts in English" and "Script texts in Spanish" for this page

## 4. Contests export

- [x] 4.1 `exportjobs.py`, `handlers/export.py`, `export.html` and its script: msgids, job texts in the starter's language, script texts from the server; tests for the job-language and script Scenarios for this page and the Spanish pin

## 5. Docs

- [ ] 5.1 Mention in the AWS language section of `docs/multi-contest.md` or the runbook (where the language selector is documented, add it if the section does not exist yet) that these pages follow the AWS language, with the Spanish `.po` and the docs check passing
