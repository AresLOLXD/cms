## Context

Observed (recon, file and line numbers in `/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/lanes/lanee/aws-i18n-recon.md`):

- The AWS machinery exists (change A): domain `aws`, `self._` and `self.n_` per handler, `gettext` and `ngettext` in the render params, `install_null_translations` for renders without a context, an `aws.pot` and a Spanish `aws.po` checked by the catalog test.
- The Spanish text lives in domain modules without a request (`bulkimport.py` builds the errors of a CSV with `%` formatting and Spanish fragments such as `LABELS`, `EMPTY_MESSAGES` and a `de el` to `del` replacement; `bulkremove.py` has `NOT_FOUND` and `NOT_IN_CONTEST`), in constants of handler modules (`bulkremove.py` handler, `contestuser.py` notices as class attributes, `export.py`), in the background jobs (`importjobs.py` and `exportjobs.py` store `job.error` in a worker thread; the browser polls it through `as_json()`), in templates (some with inline plural by `if`/`else` or suffix and a `count_of(one, many)` macro) and in two inline scripts (`contest_users_import.html`, `export.html`).
- The ranking group page mixes Spanish buttons and summaries with English labels (`WINDOW_FIELDS`, `ARCHIVE_FIELD`).
- Many tests assert on the Spanish text (about 300 assertions).
- Macros imported without context do not see the per-request `gettext`.

## Goals / Non-Goals

**Goals:**
- These pages follow the page language; the Spanish stays exactly as it is.
- Correct plurals; jobs and notifications in the language of the admin who starts them.

**Non-Goals:**
- Other AWS pages (change C); a language stored per job; plurals beyond Spanish and English; renaming data keys; translating CSV header aliases.

## Decisions

All of these are my calls unless marked as the user's.

1. **English msgids, Spanish translations (the user's).** Every Spanish string becomes an English msgid and its current text is the Spanish translation, word for word. The aws catalog test keeps the catalog complete.
2. **Domain modules take a gettext (mine).** `bulkimport.read_rows`/`plan_import`, `bulkremove.parse_usernames`/plan builders, `importjobs.IMPORT_JOBS.start` and `exportjobs` start functions get optional keyword arguments `gettext` and `ngettext` defaulting to identity; the handlers pass `self._` and `self.n_`. Messages are built with whole-sentence msgids (`%d`/`%s` placeholders), never by concatenating fragments. The `de el` to `del` hack of `bulkimport.py` is replaced by one msgid per field, whose Spanish equals today's output for that field.
3. **Constants (mine).** Module and class constants that hold text become `N_("...")` and are translated where they are shown (`self._(CONSTANT)`), so extraction sees them and import-time evaluation does not freeze a language.
4. **Jobs (the user's, with my mechanism).** The language of a job is fixed when it starts: the starter handler's `gettext` and `ngettext` are passed down to the worker, and `job.error` and the status texts are stored already translated. A second admin who polls the job sees the starter's language (documented limit). Notifications are translated with the request's `self._` before `add_notification`.
5. **Plurals (mine).** `ngettext` in Python and `{% trans count=... %}...{% pluralize %}...{% endtrans %}` in templates, replacing the `if`/`else` and suffix tricks. Spanish plural rules (one form for 1, another for the rest) come from the catalog header `Plural-Forms`.
6. **Script texts (mine).** Each page's inline script reads its texts from `data-msg-*` attributes on the existing job container (or one `data-msgs` JSON with `|tojson|forceescape`), written in the page language; plurals are sent as a pair and picked by the script. The scripts keep their logic; only the literals move. The CSV header aliases in the import script are data and stay.
7. **Data keys stay (mine).** `usuarios_nuevos`, `participaciones_nuevas`, `equipos_quitados`, `movidas_al_grupo_principal` and the like are identifiers that tests, templates and scripts read; only the labels around them are translated.
8. **Macros (mine).** Any macro touched that contains text and is imported without context gets `with context` on its imports; the touched macros are listed in the commit.
9. **Tests (mine).** The existing tests keep their intent and now assert the English message (tests run with identity translation); a smaller set per group asserts the Spanish translation using the catalog loaded from `aws.po` (like the change A tests), including one test per group that the Spanish equals the text the page showed before this change.
10. **Order (mine).** Four groups, each reviewed alone: ranking groups, bulk removal, users import with its jobs, contests export with its jobs.

## Risks / Trade-offs

- About 300 test assertions change; a wrong update could hide a real change, so each group keeps one test that pins the Spanish text of its main messages.
- A job started by an admin in Spanish shows Spanish to an admin in English who polls it.
- Whole-sentence msgids duplicate some words across messages; accepted for correct grammar.
- Plural forms for Spanish rely on the `.po` header; the catalog test checks plural entries have all forms.
- Some inline scripts compose strings by concatenation; those are converted to placeholders, which can change spacing; the Spanish pin tests catch that.
