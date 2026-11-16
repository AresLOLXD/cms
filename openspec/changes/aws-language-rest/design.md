## Context

The full consolidated design (panel of four read-only seats plus an architect tie-break, approved by the owner) is kept with the plans of this change: `/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/lanes/aws-rest-ref/CONSOLIDATED-DESIGN.md` (and the panel synthesis, the translation guide, the glossary and the checker next to it; P copies the guide, glossary and checker into `translation/` of this change). This file keeps the decisions.

State: changes A (machinery) and B (the Spanish-only pages) are merged. The remaining English is about 1,070 strings in 83 files: templates (865 findings), handlers (120), scripts (85), plus 47 texts the static scan cannot see.

## Goals / Non-Goals

**Goals:** every admin page follows the language; English output unchanged; the Spanish is consistent; the work is parallel, reviewable and provably complete.
**Non-Goals:** see the "What we will not build" list of the proposal.

## Decisions

1. **Operation buttons.** `<input type=submit name=operation value=X>` becomes `<button type=submit name=operation value="X">{% trans %}X{% endtrans %}</button>`: the posted value stays the English string the handler reads today, only the label is translated. Handlers do not change, except that the ranking group Remove compare becomes a plain `==`. The guard `test_named_controls_post_untranslated_values` is strict from the first step.
2. **Errors.** `repr(error)` stays (changing it would change English). The messages we raise are translated: the `parse_*` functions of `handlers/base.py` take a `gettext` parameter (default identity) and the handler passes `self._`; the BaseHandler methods use `self._`. Left in English: HTTP error reasons, the critical-error text, programmer errors, logs, database and library detail.
3. **Fallback to the contestant-site catalog.** Evaluation outcomes, sizes and `N/A` already have Spanish there. After loading the AWS translations, each non-English one gets the contestant-site translation of the same language as a fallback (never the shared default translation). The catalog test still requires every AWS msgid to have its own AWS entry. Plugin labels (`cms/grading` names and parameters) are out. Quick-answer labels are translated in the form; the stored values are not.
4. **Wording.** Contest is «concurso»; «username» in prose is «usuario»; «user tests» stays English; the generic «Remove» is «Quitar», with distinct titles by effect («Borrar concurso», «Quitar participación»); the «occured» typo stays; staff password is «contraseña de staff»; register is tú, neutral Latin American, impersonal for statuses. Existing catalog fixes: «fase de análisis», «fila», «concursantes», «user tests».
5. **Partition.** A prep step P and eight lanes with disjoint files, verified over the tree: L0 chrome and shared script, L1 contest settings and communication, L2 task, L3 dataset, L4 submissions and user tests, L5 users/teams/groups, L6 participations/contest tasks/activity/ranking, L7 system/admins/ranking-group leftovers (110 to 169 findings each). Nine files that are already complete are only marked.
6. **Same lane wraps and translates.** Each lane owns its files, wraps them and writes its Spanish into `cms/locale/_fragments/aws-<lane>.po` (an underscore directory that the loader skips and the package never ships); the catalog test reads the main catalog plus the fragments. Integration merges the fragments, extracts the template, refreshes the Spanish catalog without fuzzy matching, compiles, and deletes the fragments.
7. **Guards (land in P).** Per-file `aws-i18n: complete` markers switch strict checks on for a file: static template lint, Python lint, script literal scan, pseudo-localization render (every msgid becomes ⟦msgid⟧, no English word may remain outside the allow-list). Permanent checks: named controls, macros imported with context, templates compile and formats are valid, the guard catches what it promises. A temporary English snapshot test keeps English unchanged during the work and is deleted with its data at the end; the final commit removes the markers and turns the global switch on.
8. **Script channel.** Inline scripts use the existing `data-messages` JSON pattern with the page's own `fill`; `aws_utils.js` gets one `t(key, values)` fed from `base.html`; concatenated sentences become whole-sentence messages; confirmations in attributes use `tojson|forceescape`.
9. **Waves.** Two waves of four lanes (L0 L2 L4 L5, then L1 L3 L6 L7) so the second wave reuses the reviewed Spanish of the first; a fresh qa-expert reviews P, a fresh code-reviewer reviews each wave, a final architect-reviewer reviews the whole branch with a Spanish read-through.

## Risks / Trade-offs

- A wrapped text changes English (whitespace, apostrophe escaping, a `<pre>`): the English snapshot test and the conventions (`trans` for text, `_()` for attributes) cover it; the functional tests run in the final verification.
- A translated posted value breaks an operation: the button split, the strict guard and a handler test.
- Spanish drifts across lanes: seeded shared messages, one owner for shared blocks, the checker, the style test, one human read-through at the end.
- The fallback could show contestant-site wording for an AWS message: the strict catalog test requires its own entry.
- Notifications keep the language of the admin who raised them (documented limit).
- The permanent pseudo-render guard fails when an upstream sync brings new English; this is intended and documented in `docs/Localization.rst`.

## Verification plan

CI mirror (three groups and the docs check), the functional tests in rootful Docker, pyflakes and eslint, `openspec validate`, the pseudo-render report (57 pages, two modes, no English left), a real AWS smoke on localhost in Spanish (bad integer notification, task up, participation removal confirm, reevaluate confirm, quick answer stored in English, an evaluated submission), and headless Chromium screenshots of 33 pages in Spanish and English compared with the pre-change pages.
