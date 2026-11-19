# Change C (issue #30) `aws-language-rest`: consolidated design (tie-breaker)

Status: decided by the tie-breaker. The owner approves this design once, and settles the
items flagged `USER`. Code read: worktree `.worktrees/aws-es`, branch
`feat/aws-language-spanish-pages` at f95aaa0b. I checked every claim used below myself,
by grep or by running code in the worktree venv (`CMS_CONFIG=config/cms.sample.toml`).
The graph in `graphify-out/graph.json` reflects `main`, not this branch, so I read the files directly.

Facts I checked that drive the decisions:

- Operation buttons post their label. 15 `name="operation"` controls are spread over 11 templates.
  Their handlers compare against English constants: `contest.py:232-238` `REMOVE = "Remove"`,
  `task.py:530-541`, `contesttask.py:39-43,68-135` (5 values: "Remove from contest",
  "up by 1", "down by 1", "to the top", "to the bottom"), `user.py:124-135` (teams),
  `user.py:429,440` (literal "Remove", participation), `user.py:462-492` ("Delete selected
  group", "Make main group"), `rankinggroup.py:273,288` (accepts the English or the translated label).
  No unit test or functional test posts `operation`.
  The house pattern already exists: `<button type="submit" name="action" value="validate">{% trans %}Only validate{% endtrans %}</button>`
  (`contest_users_import.html:141`) and `ranking_group.html:47-54` (`visibility_action`).
- The AWS Jinja env runs old-style gettext with autoescape and the `trimmed` policy (both True). Running it showed:
  `{% trans n=x %}It's <b>{{ n }}</b>{% endtrans %}` renders `It's <b>&lt;i&gt;</b>`, so the
  text is kept raw and variables are escaped. `{{ _("It's") }}` renders `It&#39;s`.
- Jinja is 3.1.6, which has no `{% trans context %}` tag. `{{ pgettext("delete","Remove") }}` works and
  Babel extracts it with its context, but only if a `pgettext` render parameter is added.
- `get_translations()` skips directories that start with `_` (`cms/locale/locale.py:294`), so
  `cms/locale/_fragments/` is invisible at runtime.
- `format_status_text` (`cms/grading/__init__.py:57`) and `format_size` (`locale.py:201`,
  `N_("%s KiB")`...) look up `cms`-domain msgids through the request's aws `Translation`, so a Spanish admin sees them in English today.
  `babel.support.Translations.add_fallback` works: aws wins, cms is found, and unknown msgids come back unchanged.
  The test helper `aws_catalog.SPANISH` is built from `aws.po` only and has no fallback.
- Babel's `write_mo` skips empty msgstrs. So with a fallback, an aws msgid that is wrapped
  but not yet translated would show the cms.po wording. The strict catalog test closes that gap.
- 15 macro imports have no `with context` (10 files), as seat 1 listed. With 4 imports already
  using it, it is an accepted pattern in the codebase.
- 38 `repr(error)` sites. Only `bulkremove_handlers_test.py:423` asserts on one, and its file is already translated.
- The existing JS channel pattern: `{% set messages = {...: _("...")} %}` + `data-messages="{{ messages|tojson|forceescape }}"`
  + a local `fill()` with `%(name)s`, and plurals as a `[ngettext(..,1), ngettext(..,2)]` pair
  (`contest_users_import.html:97-111,254-261`, `export.html:69,160`). `aws_utils.js` is
  loaded only by `base.html:10`. login does not load it.
- Tests that pin raw English: `contestactivity_test.py:285` "Only the 1 most recent are shown"
  (English does not inflect for 1), `:322` "contest's", `contest_test.py:156...` "Invalid field(s)." (the variant with a dot,
  `contest.py:175`).
- The translation checker runs (`--selftest` ok). On `aws.po` it reports 19 findings: the 8
  real ones the guide listed plus 11 GLOSSARY false positives ("choose" in infinitive, "next",
  "visible"). So GLOSSARY must be report-only.
- CI = gevent group + docker group + asyncio group (`docker/_cms-test-internal.sh`) +
  `cmsRunFunctionalTests` (it drives AWS in English and scrapes `Compiling\.\.\.` etc.) + the docs workflow.

---

## 1. Decisions on the 8 open questions

### Q1. Operation identifiers: split the label from the value. `DECIDED`

Choice: every `<input type="submit" name="operation" value="X">` becomes
`<button type="submit" name="operation" value="X">{% trans %}X{% endtrans %}</button>`.
The `value` stays exactly the English string the handler expects today, and every other attribute
(`disabled` conditionals, `style`) is kept.

Reason: this is the house pattern (`action`, `visibility_action`). The posted value does not depend on the language,
so a language switch between page load and POST cannot break it. Handlers do not change.
Unlike the snake_case keys of the newer pages, keeping the English value as the key means
no handler, no constant and no external client that posts "Remove" has to change.
The rankinggroup pattern of accepting both values is rejected: it breaks when the language changes
mid-flight, and it puts language into a contract.

Code consequence:
- Templates: `contest.html:315`, `contests.html:13`, `tasks.html:13`, `task.html:513`,
  `teams.html:12`, `team.html:28`, `user.html:31`, `groups.html:23,30`,
  `contest_tasks.html:29,38,43,48,53`, `ranking_groups.html:16`, `ranking_group.html:101`
  (the last two post `{{ _("Remove") }}` today).
- Handlers: none, except `rankinggroup.py:287-288`, which becomes `if operation == self.REMOVE:`
  with its "posts its label" comment removed (owned by L7).
- Unnamed submits (no `name`) post nothing that matters: `value="{{ _("Activate!") }}"` is fine.
- `<option>` without a `value` attribute posts its text. If it is translated, it must get `value="<English>"`.
- Guard: `test_named_controls_post_untranslated_values` (section 4) is strict from P. Its
  allow-list starts as {ranking_group.html, ranking_groups.html} and L7 empties it.

### Q2. `repr(error)`: keep it. `DECIDED`

Reason: switching to `str(error)` changes English output at 38 sites. The owner's constraint is
"no behavior change beyond language". What gets translated is the message we raise inside the
exception (asserts, `ValueError` from `handlers/base.py`). The repr wrapper (`ValueError('…')`) shows
the translated text and stays the same in English.

Code consequence: no change to the `repr(error)` sites. Module functions in `handlers/base.py`
(`parse_string_list`, `parse_int`, `parse_timedelta_sec`, `parse_timedelta_min`, `parse_datetime`,
`parse_ip_networks`) gain a second parameter `gettext=lambda s: s`, and the message becomes
`gettext("Can't cast %s to int.") % value`. `argument_reader.helper` calls `func(value, self._)`,
and `get_string` becomes `argument_reader(lambda a, _: a, empty="")`. The only outside caller,
`rankinggroup.py:129` `parse_datetime(raw)`, keeps working through the default (L7 may pass `handler._`).
This signature change lands in P because it is shared API. BaseHandler methods (`get_bool`,
`get_time_limit`, `get_memory_limit`, `get_task_type`, `get_score_type`, `get_submission_format`,
`get_password`) translate with `self._` in L0.

Stays English on purpose: `require_permission` "Invalid permission level" (programmer error),
`HTTPError` reasons, `"A critical error has occurred :-("` (written when the locale itself failed), `describe_error(...)`
DB text, library exception text, log lines.

### Q3. Fall back to the `cms` domain for status texts and sizes: yes. Plugin labels: out. `DECIDED`

Reason: evaluation outcomes ("Output is correct", "Execution timed out"...), `N/A` and the size units
already have Spanish in `cms.po`, and they are what the contestant saw. A fallback is about 4 lines and reuses
that work. Seat 3 named three downsides. First, it would hide missing aws msgids: the strict catalog test
checks `aws.po` itself, not the runtime, so nothing is hidden. Second, it mixes vocabularies: only
cms-only msgids reach the fallback once `aws.po` is complete. Third, the test helper has no fallback: we add the same line to it.

Code consequence (P):
- `cms/server/admin/server.py` after `self.translations = get_translations("aws")`:
  `cms = get_translations()` and, for every `code, t` in `self.translations` with
  `t is not DEFAULT_TRANSLATION and code in cms`, call
  `t.translation.add_fallback(cms[code].translation)`. Never touch `DEFAULT_TRANSLATION`: it is shared by both domains.
- `aws_catalog.py`: after building SPANISH, call `SPANISH.translation.add_fallback(get_translations()["es"].translation)`.
- Test (P, `aws_fallback_test.py`): with SPANISH, `format_status_text(["Output is correct"], SPANISH)` gives
  the cms.po Spanish, an aws msgid still uses aws.po, and English is unchanged.

Plugin labels are OUT of scope. `TASK_TYPES`/`SCORE_TYPES`/`LANGUAGES` names are identifiers.
`ParameterType` names, descriptions and choice labels are defined in `cms/grading` and rendered by
`ParameterType.render` with its own template. Translating them means threading a translation through
`cms/grading` and would leave half-translated tables in between. They go in the proposal's "will not build" list.
The pseudo-render allow-list carries them. Exception: the Question quick-answer **labels** in the
`<select>` (`macro/question.html:94`) become `{{ _(value) }}`, with `N_()` markers in `handlers/contestquestion.py`.
The posted key and the stored `reply_subject` stay English (L1).

### Q4. Glossary and wording

| # | Item | Flag | Decision / recommendation |
|---|---|---|---|
| a | contest = concurso or competencia | `USER` | Recommend **concurso**: it has 30 uses in aws.po, the guide and checker are built on it, and there is no rework. cms.po (competencia) stays as is |
| b | "username" in prose | `USER` | Recommend **usuario** in prose. The literal key `(username)` and CSV headers stay. The existing "Un username por línea…" gets fixed in P |
| c | "user tests" | `USER` | Recommend keeping **user tests** in English, as the brief says. The one outlier "pruebas de usuario" gets fixed in P |
| d | The bare "Remove" button deletes (contest, task, team, ranking group) or detaches (participation) | `USER` | Recommend: the shared msgids "Remove" / "Yes, remove" stay **Quitar** / **Sí, quitar**, which mirrors the equally ambiguous English. Distinct msgids are translated by their effect: "Remove Contest" = "Borrar concurso", "Remove User" = "Borrar usuario", "Remove Participation" = "Quitar participación". Option if you want "Borrar" on the delete buttons: `{{ pgettext("delete", "Remove") }}` on 8 buttons + `params["pgettext"] = self.translation.translation.pgettext` in `render_params` (verified to work and to extract). Renaming the English to "Delete" (seat 4's idea) is rejected because it changes visible English |
| e | msgid typo "occured" (`admin/templates/error.html:9`) | `USER` | Recommend **leave it**: the fix changes visible English, the msgid is shared with CWS `error.html:14`, and a test pins its absence in Spanish. It belongs in a separate upstream-facing fix. No other msgid typos were found in admin sources |
| f | staff password wording | `DECIDED` | "contraseña de staff" (aws.po uses it in all 5 places; RWS is out of scope) |
| g | Existing aws.po fixes: modo→**fase de análisis** (3), renglón→**fila** (2), pruebas de usuario→user tests (1), participantes→**concursantes** (1), and (b) if approved | `DECIDED` (b subject to USER) | Done in P, with the Spanish pins in `translated_pages_test.py` and the export/import page tests updated in the same commit |
| h | Register | `DECIDED` | tú, neutral Latin American, impersonal `se` for statuses; never usted |

### Q5. Lanes and guards: P + 8 lanes, guards in P, per-file markers, a global strict switch at the end. `DECIDED`

Reason: I ran the inventory (1,070 findings over 83 files) against the partition: the 8 lanes are disjoint
and cover every file with findings (section 2). Each weighs 110-169, which is one implementer each.
Fewer, larger lanes would make lanes of about 300 strings and lose the calibration between waves.
Per-file markers let each lane switch strict mode on for its own files without editing a shared list.
Lanes run in two waves of four (Q8/section 7).

### Q6. Wrapping and translation happen in the same lane. `DECIDED`

Reason: a lane owns its files, so it freezes its own msgids. The msgids shared across lanes
are seeded in P (section 2), and the later wave reuses what the earlier wave merged.
Splitting job A from job B would serialize all 8 lanes behind one wrapping batch and make two review cycles.
The catalog test is strict about fragments, so every lane commit is green only when its new msgids
are translated. Commit granularity: one commit per file group ("contest.html and its Spanish"), each green.
English safety comes from the English snapshot test, not from a separate wrapping phase.

### Q7. Out of scope: see section 5, "What we will not build". `DECIDED` (the owner can veto at approval)

### Q8. Risks and verification: see section 6. `DECIDED`

**What "English stays byte-identical" means here.** The visible English text (text nodes, title,
placeholder, alt, aria-label, button labels) and every posted form value are unchanged. Markup may change only in
these listed ways: `<input type=submit>` becomes `<button>`, `with context`, the data-messages attributes,
JS literals becoming `tojson` output, and the temporary markers. The P snapshot test enforces it.

---

## 2. Final partition

Inventory: QA seat's linters on f95aaa0b (static template lint + JS literal scan + Python AST lint),
checked by my script (`scratchpad/tie/partition.py`): no file is in two lanes, no file with findings is left unassigned.

| Lane | Files (findings) | Total |
|---|---|---|
| **L0** shared chrome + script channel | base.html 49, login.html 5, error.html 0, fragments/lang_switch.html 4, fragments/overload_warning.html 2, fragments/theme_switch.html 0, static/aws_utils.js 37, static/web_rpc.js 1, handlers/base.py 25 (minus the P part), handlers/main.py 0, server.py 0 (P adds the fallback), jinja2_toolbox.py 0, formatting.py 0 (`format_dataset_attrs` " (Live)"/" (Background judging)", the clock-zone labels) | 123 |
| **L1** contest settings + communication | contest.html 77, contests.html 9, add_contest.html 3, contest_remove.html 7, announcements.html 10, questions.html 2, fragments/group_settings.html 14, macro/question.html 19, macro/markdown_input.html 2, handlers/contest.py 7, handlers/contestannouncement.py 1, handlers/contestquestion.py 0 (+N_ quick-answer labels) | 151 |
| **L2** task | task.html 130 (incl. 9 JS), tasks.html 5, add_task.html 3, task_remove.html 7, add_statement.html 3, add_attachment.html 2, handlers/task.py 19 | 169 |
| **L3** dataset | add_dataset.html 29, activate_dataset.html 30, delete_dataset.html 4, rename_dataset.html 3, add_testcase.html 6, add_testcases.html 7, download_testcases.html 5, add_manager.html 2, dataset.html 2, fragments/dataset_selector.html 4, handlers/dataset.py 21 | 113 |
| **L4** submissions + user tests | submission.html 53, user_test.html 24, contest_submissions.html 2, contest_user_tests.html 2, fragments/user_test_row.html 7, fragments/user_test_rows.html 7, macro/submission.html 31, macro/reevaluation_buttons.html 6, macro/pages.html 3, handlers/submission.py 3, handlers/usertest.py 0, handlers/contestsubmission.py 0 | 138 |
| **L5** users / teams / groups | user.html 33, add_user.html 19, user_remove.html 7, users.html 4, users_bulk_remove.html 2, team.html 6, teams.html 5, add_team.html 5, team_remove.html 7, groups.html 14, group.html 11, fragments/hashed_password_form.html 4, handlers/user.py 28 | 145 |
| **L6** participations, contest tasks, activity, ranking | participation.html 40, participation_remove.html 7, contest_users.html 8, contest_tasks.html 13, contest_activity.html 19, macro/activity.html 15, ranking.html 8, handlers/contestuser.py 5, handlers/contesttask.py 5, handlers/contestactivity.py 1, handlers/contestranking.py 0 | 121 |
| **L7** system, admins, ranking-group leftovers | overview.html 43 (18 JS), resources.html 27 (14 JS), resourceslist.html 3, admins.html 10, admin.html 5, add_admin.html 4, fragments/admin_form.html 6, handlers/admin.py 5, ranking_group_remove.html 7, ranking_group.html 0, ranking_groups.html 0, add_ranking_group.html 0 (button split, "(UTC)" leftovers), handlers/rankinggroup.py 0 (the `==` simplification) | 110 |

Files with no lane, all already complete. P marks them: `bulkimport.py`, `bulkremove.py`, `exportjobs.py`,
`importjobs.py`, `rpc_authorization.py`, `handlers/bulkremove.py`, `handlers/export.py`,
`templates/contest_users_import.html`, `templates/export.html`. Excluded from the guards:
`__init__.py` files and `templates/ranking.txt` (an export format, out of scope).

**Test files each lane may edit** (existing tests should need no edit, because English is unchanged; this is only in case):
L0 `admin_session_test, basehandler_*, body_limits_test, formatting_test, lang_switch_test,
language_test, multipart_form_limit_test, notifications_test, pool_exhaustion_test,
rpc_authorization_test, server_test, theme_aws_test, try_commit_test, utc_time_inputs_test,
xsrf_error_page_test`; L1 `contest_test`; L2 `taskpage_warning_test`; L3 `clonedataset_test,
dataset_forms_test, dataset_upload_test`; L6 `contestactivity_test, contestactivity_csv_test,
contestranking_test`; L7 `rankinggroup_test`. `bulkremove_lists_test` is shared by L5 and L6: neither edits it.
Each lane adds one new file, `cmstestsuite/unit_tests/server/admin/translated_<lane>_test.py`
(e.g. `translated_contest_test.py`).

**Shared files no lane edits:** `aws.po`, `aws.pot`, `*.mo`, `catalog_test.py`, `aws_catalog.py`,
`aws_i18n_guard_test.py`, `aws_i18n_lint.py`, the snapshot JSON, `tasks.md`, docs, other lanes' plans.
A lane writes only its owned files, `cms/locale/_fragments/aws-<lane>.po`, its new test file and its own plan.

### Prep step P (one implementer, one reviewed batch, before any lane)

Pre-condition: change B (`aws-language-spanish-pages`) is verified, archived and merged into local `main`.
C branches from `main` as `feat/aws-language-rest` (the integration branch). Two open changes on the same `aws-language` spec would tangle the archives.

P commits, in this order:
1. **English snapshot**, generated on the untouched tree: `cmstestsuite/unit_tests/server/admin/data/aws_english_snapshot.json`
   + `test_english_render_unchanged` (section 4). It must be generated before any template edit.
2. **`with context`** on the 15 imports (activate_dataset.html:1; dataset.html:1,4; announcements.html:2;
   contest_activity.html:2; contest_submissions.html:1,4; submission.html:1; fragments/user_test_rows.html:1;
   participation.html:1,4,6; macro/question.html:3; macro/submission.html:1,2) + `test_macro_imports_take_the_context`.
3. **parse_* gettext parameter** (Q2) + their 6 msgids with Spanish in aws.po, plus one test: `get_int` on "abc" with SPANISH raises the Spanish text.
4. **cms fallback** (Q3) in server.py + aws_catalog.py + `aws_fallback_test.py`.
5. **Catalog machinery**: `catalog_test.py` treats `aws.po ∪ cms/locale/_fragments/aws-*.po` as the es
   catalog and `aws.pot ∪ fragment msgids` as the template. New tests: a fragment must not repeat a msgid already in aws.po,
   and must not repeat a msgid of another fragment with a different msgstr. Babel `catalog.check()` runs on es aws.po + fragments.
   `test_aws_es_style` is strict on the mechanical codes (section 4). `aws_catalog.SPANISH` merges the fragments.
6. **Guards**: `aws_i18n_lint.py` (the helper module, cleaned from the QA prototypes) + `aws_i18n_guard_test.py`, with markers on the 9 complete files.
7. **Seeds** in aws.po (msgid + Spanish, not in the pot): the msgids used by 2 or more lanes, measured on the inventory.
   Already present and reused: Invalid field(s), Reset, Name, Update, Remove, Delete, Description, First name, Last name, Password,
   Username, Users, Contests, Submissions, This operation cannot be undone., Invalid operation %s.
   New: Activity, Announcements, Codename, Create, Disabled, Enabled, Group, Id, Logout, Memory limit,
   No, Yes, Yes, remove, Operation failed., Operation successful., Output, Overview, Public, Questions,
   Ranking, Send, Settings, Status, Tasks, Time, Time limit, Timezone, Title, Upload, User, User tests,
   Users list, Please select a valid operation, No username specified., No questions., Subject:, Text:,
   Dataset:, By:, [View results], Invalid field(s)., Shard. Seeds no lane ends up using are dropped at integration (`--ignore-obsolete`).
   P also lists the shared sentence blocks: the token parameters (contest.html and task.html) and the limits / task type / score type
   settings (task.html and add_dataset.html). Their canonical owner is **L2** (wave 1). L1 and L3 (wave 2) reuse L2's merged msgids and Spanish verbatim.
8. **aws.po fixes** (Q4 g) + updated Spanish pins.
9. **Translation reference** copied into the change: `openspec/changes/aws-language-rest/translation/`
   `{guide.md, glossary.json, check.py}`, with the Q4 decisions applied and the GLOSSARY allow-list for the 11 false positives.

P gets its own OpenSpec task group, plan and fresh review (section 7).

### Merge order and integration (per wave)

Wave 1 merges into `feat/aws-language-rest` in the order L0, L4, L2, L5. Wave 2, branched from the wave-1 integration, merges L1, L3, L6, L7.
The lanes are disjoint, so the order only matters for the fragment tie-break (earlier wins; L2 wins within wave 1). Integration steps, run by the coordinator:
1. `git merge --no-ff` each lane. Expected conflicts: none. A conflict means a lane broke its file list, and goes back to that lane.
2. `./setup.py extract_messages -F babel_mapping_aws.cfg -o cms/locale/aws.pot` (docs/Localization.rst).
3. `./setup.py update_catalog -D aws -i cms/locale/aws.pot -l es --no-fuzzy-matching --ignore-obsolete`.
4. Fill the empty msgstrs from the fragments with a ~15-line Babel script, kept in the plan and not committed. It stops on any
   msgid that has two different msgstrs; the coordinator picks per the glossary and owner rule.
5. `./setup.py compile_catalog -l es`. Delete `cms/locale/_fragments/`.
6. Run the checker on aws.po (0 mechanical findings; GLOSSARY read by a human), the catalog tests, the guard,
   the snapshot test, all `translated_*_test.py`, and the lanes' existing test files. Tick `tasks.md`.

### Lane definition of done

- Every owned file carries the marker, and all marker-scoped guards pass for it: static template lint, Python lint,
  JS literal scan, pseudo-render of owned pages (leftovers allowed only from unmarked files and the allow-list).
- `test_english_render_unchanged` and `test_named_controls_post_untranslated_values` are green.
- `catalog_test.py` is green: the fragment holds every new msgid, nothing is fuzzy or empty, no seed or merged msgid is repeated,
  `check()` and the style test are clean.
- `translation/check.py` on the fragment: 0 PLACEHOLDER/MARKUP/REGISTER/ELLIPSIS/INVERTED findings, and the GLOSSARY
  findings are listed in the lane report with a one-word verdict each.
- `translated_<lane>_test.py`: at least one Spanish pin per page group (3 to 5 strings each), plus one English-unchanged
  assertion per handler whose notifications changed, each commented with its Scenario.
- The lane's existing tests pass unchanged, plus pyflakes and eslint (JS lanes L0, L2, L5, L7).
- `git diff --stat main...lane` touches only the lane's file list, its fragment, its test file and its plan.
  `tools/check_lane.py <lane>` checks it against `tools/lanes.json`. A lane may not add a page template or a
  macro: the snapshot has no entry for it and the guards stop with a message (ask the coordinator).

---

## 3. Shared conventions (every implementer prompt carries all of this)

1. **Text nodes** use `{% trans %}…{% endtrans %}`. Variables are passed as `{% trans name=expr %}…{{ name }}…{% endtrans %}`.
   A trans block cannot hold `{% if %}`, filters or attribute access, so compute them in the trans arguments.
   Wrap whole sentences, never fragments. Inline markup (`<a>`, `<code>`, `<b>`, `<em>`) may sit inside the msgid.
   Text with an apostrophe always uses trans, because `{{ _() }}` would emit `&#39;` and break raw-HTML tests.
2. **Attributes** (title, placeholder, alt, aria-label, the value of unnamed submits) use `value="{{ _("…") }}"`.
   String literals in `{{ "…" }}` / `{% set x = "…" %}` that are shown become `_("…")`.
3. **Whitespace-sensitive content** (`<pre>`, `<textarea>` defaults) uses `{% trans notrimmed %}` or
   stays outside the msgid. `trimmed` collapses newlines.
4. **Plurals**: templates use `{% trans count=n %}…{{ count }}…{% pluralize %}…{% endtrans %}`. Python uses
   `self.n_(singular, plural, n) % {"count": n}`. When the English does not inflect (for example "Only the
   %(count)s most recent are shown"), both English forms are identical so English stays the same while Spanish agrees.
   0 takes the plural.
5. **Python**: `self._("…")` in handlers. Always `self._("… %s") % x`, never `self._("… %s" % x)`.
   f-strings become `%` msgids, with named placeholders when there is more than one. Module-level texts use
   `N_("…")` and are translated at use with `self._(CONST)`. Module functions take a `gettext` parameter, as `parse_*` does.
   No new render parameters.
6. **Notifications**: `self.service.add_notification(make_datetime(), self._("Subject"), self._("Text") or repr(error))`.
   The text is stored in the actor's language, and the global queue delivers it to whoever polls. This is a known limit; do not fix it.
7. **Errors**: translate assert messages and the `ValueError`s we raise for the admin. Keep `repr(error)`.
   Leave English: HTTPError reasons, logs, programmer errors, `cms/db` / `cms/grading` validation detail
   (translate only our prefix), library exception text, CSV header formats.
8. **Operation identifiers** (Q1): `<button type="submit" name="operation" value="<English as today>">{% trans %}<label>{% endtrans %}</button>`.
   Never translate a `value` that a handler reads. An `<option>` whose text is translated gets an explicit English `value`.
9. **Script texts**:
   - Inline template scripts with several messages use the house pattern: `{% set messages = {"key": _("…"), "pair": [ngettext(s,p,1), ngettext(s,p,2)]} %}`,
     `data-messages="{{ messages|tojson|forceescape }}"` on the page's container, `JSON.parse(el.getAttribute("data-messages"))`,
     and the page's own `fill(text, values)` for `%(name)s`.
   - A single literal in an inline script uses `{{ _("…")|tojson }}`.
   - In an event attribute: `onclick="confirm({{ _("Do you really want to reevaluate?")|tojson|forceescape }}) && …"`.
   - `static/aws_utils.js` (L0 only): `base.html` gets `{% set aws_messages = {...} %}` and `<body class="admin" data-messages="{{ aws_messages|tojson|forceescape }}">`.
     `CMS.AWSUtils` gains one `t(key, values)` that reads the attribute lazily, fills `%(name)s` in one pass and returns the key if it is missing.
     Concatenated sentences ("the <a…>", "of <a…>") become whole-sentence msgids with placeholders.
   - No JS i18n library, no plural rules beyond the [one, many] pair, no new Python module, and no refactor of the existing `fill()` copies.
10. **Macros**: imports always use `with context` (P did the 15). A new import without it fails the guard.
11. **Markers**: the first line of each owned file, written when the file is complete:
    `{# aws-i18n: complete #}` (templates), `# aws-i18n: complete` (py, anywhere in the file),
    `// aws-i18n: complete` (js). Marking a file switches its guards to strict. The final integration commit deletes all markers.
12. **Fragment**: `cms/locale/_fragments/aws-<lane>.po` (e.g. `aws-L3.po`). It is a valid PO with the header
    `Language: es`, `Plural-Forms: nplurals=2; plural=(n != 1);` and UTF-8. It holds only msgids that are not in aws.po
    (seeds and wave-1 merges are reused, never repeated), all translated, none fuzzy, no `#:` locations needed.
    Your to-do list is the failure message of `test_code_messages_are_in_pot_and_es`.
13. **Spanish**: follow `openspec/changes/aws-language-rest/translation/guide.md` and `glossary.json`. Use tú imperatives in prose,
    infinitives on buttons, nouns for headers, `¿…?`, `…` (U+2026), «» around quoted labels, markup and placeholders kept, sentence case.
    "user tests", dataset, worker, staff, token, UTC and units stay. A new term goes into glossary.json in the lane report
    first (the coordinator merges it); do not invent a parallel term.
14. **Checker**: `python3 -I openspec/changes/aws-language-rest/translation/check.py cms/locale/_fragments/aws-<lane>.po` before every commit.
15. **Process**: work in your own worktree, branch and DB (`cmstest_aws_<lane>` on the shared podman PG `:55432`).
    Commit messages in English, Conventional Commits (`feat(aws): translate the task page and its handler`).
    Do not edit shared files (section 2). Ponytail rules apply: the smallest complete change, no options,
    one test per new logic, and a `shortcut:` comment for a known limit.

---

## 4. Guard tests (land in P) and integration checks

File `cmstestsuite/unit_tests/server/admin/aws_i18n_guard_test.py` with helper `aws_i18n_lint.py`.
No DB is needed; it runs in the asyncio group and takes about 2 s.

| Test | Behavior | Mode during lanes | After final commit |
|---|---|---|---|
| `test_english_render_unchanged` | Renders every page template with the identity translation and the permissive stand-ins (full and empty modes). Compares the whitespace-normalized visible texts (text nodes + title/placeholder/alt/aria-label + submit/button labels) and the sorted posted `(name, value)` pairs with the P snapshot | strict, all pages | **deleted** together with its JSON (a permanent snapshot would block every legitimate future English edit) |
| `test_named_controls_post_untranslated_values` | Pseudo-render (msgid → `⟦msgid⟧`): no named input/button/option posts a value that contains `⟦` | strict; allow-list {ranking_group.html, ranking_groups.html} until L7 | strict, empty allow-list, permanent |
| `test_macro_imports_take_the_context` | Every `{% import %}` / `{% from … import %}` in admin templates has `with context` | strict | permanent |
| `test_template_text_is_wrapped[file]` | Static lint: text nodes, the 4 attributes, unnamed submit values, `{{ "literal" }}`, `{% set %}` literals, confirm/alert literals in event attributes. Allow-list `(file, text, reason)` in the helper | strict for marked files, skip otherwise | strict for all files (globbed) |
| `test_script_literals_are_wrapped[file]` | JS prose literals in `static/*.js` (excluding vendored jq and prism) and inline scripts | marked only | all |
| `test_python_texts_are_wrapped[file]` | AST lint over `cms/server/admin/**.py`: literal notification subjects/texts, assert messages, `ValueError` messages, `self.write` literals, module text constants without `N_`. Allow-list for intentional English (HTTPError, the permission check, the critical-error write, logs) | marked only | all |
| `test_pseudo_render_leaves_no_english[page]` | The page rendered in pseudo-locale has no English words left, except texts owned by unmarked files and the allow-list (cms/grading names and parameters, language names, "español", data placeholders) | marked pages | all pages, permanent (upstream syncs that bring English fail here by design; documented) |
| `test_templates_compile_and_formats_are_valid` | Every template parses, and every `%`-format in msgids is consistent between trans variables and Python `%` | strict | permanent |
| `test_the_guard_catches_what_it_promises` | Self-check: a synthetic template with one wrapped and one bare text, a bare title, a bare notification and a bare JS literal yields exactly those findings | strict | permanent |
| `test_every_file_is_complete` | Skips with "pending=N" until the final commit, then becomes the strict switch (the marker-free global mode above) | skip (reports N) | strict |

Catalog side (`cmstestsuite/unit_tests/locale/catalog_test.py`, P):
- The existing open/missing tests become fragment-aware: `test_fragments_do_not_repeat_merged_msgids`, `test_fragments_agree_with_each_other`.
- `test_es_catalogs_pass_babel_checks`: `catalog.check()` (python-format placeholders) on es cms, aws, fragments.
- `test_aws_es_style`: strict on msgstr: no usted forms (regex list taken from check.py REGISTER), no `...`,
  `?`/`!` paired with `¿`/`¡`, markup tags and entities equal to the msgid's. GLOSSARY is not in the test: the checker reports it and humans review it.
- `test_fallback_reaches_cms_only_messages` (in `aws_fallback_test.py`).

Lane tests: `translated_<lane>_test.py` with Spanish pins and English-unchanged checks for changed handlers. One
operation handler test in L6 (Scenario "An operation posted from a Spanish page"): render `contest_tasks.html` with SPANISH,
read the button values and post "up by 1" to `ContestTasksHandler`; the task moves.

Integration checks (each wave and final): section 2's integration steps, plus `pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite`,
`eslint cms/server/admin/static cms/server/contest/static`, `openspec validate aws-language-rest`.

---

## 5. OpenSpec scope: change `aws-language-rest`, delta on capability `aws-language`

Plans: `openspec/changes/aws-language-rest/plans/2026-11-DD-{prep,L0,…,L7,integration}.md`.
`tasks.md` groups: 1 Prep, 2 Wave 1 (L0, L4, L2, L5 + integration), 3 Wave 2 (L1, L3, L6, L7 + integration), 4 Final (strict switch, docs, verification).

**ADDED Requirement: Every admin page follows the AWS language.**
- Scenario *A page group in Spanish*: with Spanish chosen, each page group (chrome, contest, task,
  dataset, submissions/user tests, users/teams/groups, participations/activity/ranking, system/admins) shows no English
  outside the documented exceptions. Tests: `test_pseudo_render_leaves_no_english` + the `translated_<lane>_test.py` pins.
- Scenario *A page in English is unchanged*: every page's visible English text and posted values equal the
  pre-change ones. Tests: `test_english_render_unchanged` (during the change) + the existing page tests + functional tests.
- Scenario *A new untranslated text*: a template, script or handler text added without gettext fails the check with
  its file and text. Test: `test_the_guard_catches_what_it_promises`, `test_every_file_is_complete`.

**ADDED Requirement: Operation buttons post the same value in every language.**
- Scenario *An operation posted from a Spanish page*: the Spanish label "Subir 1" posts "up by 1" and the task moves. Test: L6 handler test.
- Scenario *No translated posted values*: Test: `test_named_controls_post_untranslated_values`.

**ADDED Requirement: Notifications and form errors use the admin's language.**
- Scenario *Invalid field in Spanish*: a bad integer gives "Campos no válidos" and `ValueError('No se puede …')`. Test: P parse test + L0.
- Scenario *Invalid field in English*: unchanged text, including `repr(error)`. Tests: existing `contest_test`, `try_commit_test`.
- Scenario *A notification read by another admin*: it keeps the actor's language (documented limit). Test: L0 notification test.

**ADDED Requirement: Script texts follow the AWS language.**
- Scenario *A shared script message in Spanish*: `CMS.AWSUtils.t` returns the Spanish text from the page. Test: L0 (static
  ratchet: every `t("key")` exists in `base.html`'s dict and the reverse; render base.html with SPANISH and check the parsed data-messages).
- Scenario *A confirmation in Spanish*: the reevaluate confirm shows the Spanish question. Test: L4.

**ADDED Requirement: Evaluation outcomes and sizes appear in the admin's language.**
- Scenario *Outcome in Spanish*: "Output is correct" shows the cms.po Spanish on the submission page. Test: `aws_fallback_test.py` + L4 pin.
- Scenario *Outcome in English*: unchanged. Test: `aws_fallback_test.py`.

**ADDED Requirement: Quick answers are labeled in the admin's language and stored unchanged.**
- Scenario *A quick answer from a Spanish page*: label "Sí", stored `reply_subject` "Yes". Test: L1.

**MODIFIED Requirement (existing catalog requirement): the Spanish catalog is complete and follows the style guide.**
- Scenario *Spanish register*: no usted forms, Spanish punctuation, placeholders and markup kept. Test: `test_aws_es_style`, `test_es_catalogs_pass_babel_checks`.

**What we will not build** (goes verbatim into the proposal):
1. No change to any English text: no "occured" fix, no "Remove"→"Delete" rename, no `str(error)`.
2. No translation of `cms/grading` names and parameters: task/score type names, `ParameterType` names, descriptions and choices, score-type parameter help, language names.
3. No translation of stored or exported data: `Question.QUICK_ANSWERS` values / `reply_subject`, announcement and message bodies, `ranking.txt`, activity CSV headers, DB validation detail, library exception text.
4. No translation of HTTP error reasons, the critical-error fallback text or log messages.
5. No per-admin language for notifications: the global queue and actor-language text stay.
6. No `pgettext` machinery (unless the owner picks "Borrar" in Q4 d).
7. No change to `cms.po` or CWS, no RWS wording, no new languages.
8. No JS i18n library, no JS plural engine, no refactor of the existing `fill()` copies or of the import/export page scripts.
9. No CSS or visual change beyond `<input type=submit>` → `<button>` on the operation controls.
10. No permanent English snapshot; no committed fragment-merge script.

---

## 6. Risk register and verification plan

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| 1 | A wrapped text changes English (whitespace, apostrophe escaping, a split sentence, a `<pre>` collapse) and breaks functional-test regexes or pins | high / high | `test_english_render_unchanged` strict from P; conventions 1 and 3; functional tests in the final CI mirror |
| 2 | A translated posted value breaks an operation | medium / high | Q1 split; strict guard; L6 handler test; functional tests (AWS in English) |
| 3 | Spanish drifts across 8 implementers | high / medium | Seeds in P; L2 owns the shared blocks; wave 2 reuses merged Spanish; glossary + checker; style test; one human read-through of the whole aws.po diff at the final review |
| 4 | A lane edits a shared file or another lane's file | medium / medium | File lists in the prompts; diff-stat gate in the DoD; merge conflict = sent back |
| 5 | The catalog union fails at integration (duplicate msgids with different Spanish, obsolete seeds, a lost plural) | medium / medium | Fragment-agreement test during lanes; merge script stops on conflicts; `--no-fuzzy-matching --ignore-obsolete`; strict catalog test after compile |
| 6 | The cms fallback shows cms wording for an aws msgid (e.g. "Username" → "Nombre de usuario") | low after integration / low | Strict catalog test (every aws msgid has its own msgstr); fallback test |
| 7 | The pseudo-render stand-ins miss a branch (the guard passes, English is left) | medium / low | Static lint as the second guard; full and empty modes; Chromium screenshots on a real DB |
| 8 | A JS channel bug: a missing data-messages attribute or a broken JSON escape leaves blank alerts | medium / medium | `t()` returns the key when missing; ratchet test; `tojson|forceescape` only; screenshots exercise overview/resources/task JS in es and en |
| 9 | Notifications in mixed languages between admins | certain / low | Documented limit (Scenario); not fixed |
| 10 | DB contention / CI budget: 4 lanes on one PostgreSQL; pushes | low / medium | Private DB per lane; lanes run only their own tests plus guards (DB-free); the full CI mirror only at integration; one push at the end, with the owner's OK, then watch ci+docs |

Others noted (not top 10): `with context` render cost (measured negligible); upstream syncs failing the permanent
pseudo-render guard (intended; documented in docs/Localization.rst); B not merged first (pre-condition).

**Verification plan (final, before archive):**
1. CI mirror locally: the gevent group, the docker group and the asyncio group exactly as in `docker/_cms-test-internal.sh`;
   `cmsRunFunctionalTests -v` in the rootful Docker setup (isolate); pyflakes; eslint; the docs build
   (Sphinx + the es/en manual `.po` follow any English edit in `docs/Localization.rst`).
2. `openspec validate aws-language-rest` + the Scenario ↔ test table in the verify report.
3. Pseudo-render report: 57 pages × 2 modes, 0 leftovers outside the allow-list (attach the summary).
4. Real AWS smoke on localhost only. Start the services against a private DB (`cmstest_aws_smoke`) loaded by
   `cmsRunFunctionalTests` (contest, tasks, users, submissions, user tests) or the testsuite's contest loader. Exercise in Spanish:
   save the contest form with a bad integer (notification), move a task up (operation), remove a participation (confirm page),
   reevaluate (confirm), answer a question with a quick answer (DB shows "Yes"), and open an evaluated submission (outcome in Spanish).
5. Headless Chromium screenshots (Playwright Chromium + puppeteer-core, light and dark not needed), each in **es** and **en**
   (cookie `aws_language`): login, overview, resources, contests list, contest, announcements, questions, contest users,
   participation, contest tasks, contest submissions, submission detail, contest user tests, user test detail, activity,
   ranking, tasks list, task, add dataset, activate dataset, dataset/testcases, users list, user, add user, teams, team,
   groups, admins, admin, ranking groups, ranking group, one remove page (contest_remove), the error page.
   That is 33 pages × 2 = 66 PNGs. Read them; en must look identical to the screenshots of the pre-change `main`
   (take 5 baseline shots there: overview, contest, task, submission, user).

---

## 7. Timeline of waves and review gates

| Step | Parallel work | Gate |
|---|---|---|
| W-1 | Finish B: verify, archive, merge to local `main` (if not already done) | B's own flow |
| W0 propose | `/opsx:propose aws-language-rest` from this design (proposal, delta spec of section 5, design.md, tasks.md); owner approval of the design + USER items | **Owner approves** (the only design stop) |
| W0 P | 1 implementer (python-pro) | Fresh **qa-expert** review (it introduces the test strategy and guards) → fix loop; ponytail-debt list; P merged into `feat/aws-language-rest` |
| W1 | **4 lanes at once**: L0, L2, L4, L5 (python-pro; L0 with a JS-capable specialist if the agent list has one, else python-pro) | Per lane: ponytail-review inline + checker report + diff-stat gate → wave-1 integration (section 2) → one fresh **code-reviewer** over the integrated wave diff (the lane-group review) → fix loop by the lane implementers |
| W2 | **4 lanes at once**: L1, L3, L6, L7, branched from the wave-1 integration (they reuse L2's/L5's merged Spanish) | Same gates as W1; the wave-1 review's conventions corrections are added to the W2 prompts |
| W3 final | Coordinator + 1 implementer: delete markers and the snapshot test + JSON, switch `test_every_file_is_complete` on, docs (`docs/Localization.rst`: all AWS pages follow the language, the guard; the manual + `.po`), verification plan 1-5 | Final whole-branch fresh review (**architect-reviewer** for structure plus a Spanish read-through of the aws.po diff in the same seat) → verify report → `/opsx:archive` (own commit) → merge into local `main`; push only per the batch rule and with the owner's OK |

Why 4 at once rather than 8: there is one PostgreSQL, and the review fan-in stays at 4 diffs. Most of all, wave 1
calibrates the conventions (wording, JS channel, plurals), so wave 2 starts from merged, reviewed Spanish.
That removes the L1/L2 and L2/L3 duplicate sentence blocks by construction. The machine can take 8. If the owner wants
it faster, run all 8 at once after P and accept integration-time msgstr conflicts on the shared token/limit blocks (L2 wins).

Dispatch order inside a wave: start the largest lanes first (W1: L2 169, L5 145, L4 138, L0 123; W2: L1 151, L6 121, L3 113, L7 110).

---

## Summary of decisions

1. Operation buttons become `<button name="operation" value="<the English value as today>">` + a translated label. No handler changes except rankinggroup.py:288 → `==`. Guard strict from P.
2. `repr(error)` is kept. Our raised messages are translated: `parse_*` get a `gettext` parameter (in P), and BaseHandler methods use `self._`.
3. The cms-domain fallback is added in server.py and aws_catalog.py (status texts, sizes, N/A). cms/grading plugin labels are out. Quick-answer labels are translated, stored values are not.
4. P + 8 disjoint lanes (verified, 110-169 findings each), 9 complete files marked in P, fragments in `cms/locale/_fragments/`.
5. Wrapping and Spanish happen in the same lane, one green commit per file group. English safety comes from a temporary English snapshot test.
6. Guards land in P: marker-scoped strict per file, then a global strict switch and marker removal in the final commit. The pseudo-render, named-controls and with-context guards stay permanently.
7. Script texts follow the house data-messages pattern. aws_utils.js gets one `t()` fed by base.html.
8. Two waves of 4 (L0 L2 L4 L5, then L1 L3 L6 L7). Fresh qa-expert on P, fresh code-reviewer per wave, final architect-reviewer + Spanish read-through.
USER items (my recommendation in brackets):
- U1 contest term [concurso]
- U2 "username" in prose [usuario]
- U3 user tests [keep English]
- U4 bare "Remove" button [Quitar, distinct titles by effect; pgettext option for Borrar]
- U5 the "occured" typo [leave]
