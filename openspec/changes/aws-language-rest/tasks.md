## 1. Prep (P): one implementer, one reviewed batch, before any lane

- [x] 1.1 English snapshot of every page generated on the untouched tree, and `test_english_render_unchanged`
- [x] 1.2 `with context` on the 15 macro imports and `test_macro_imports_take_the_context`
- [x] 1.3 `gettext` parameter for the `parse_*` functions of `handlers/base.py` with their six messages in Spanish, and a test (Scenarios "Invalid field in Spanish", "Invalid field in English")
- [x] 1.4 Contestant-site fallback in the AWS translations and the test helper, with `aws_fallback_test.py` (Scenarios "Outcome in Spanish", "Outcome in English")
- [x] 1.5 Catalog machinery: the catalog test reads `aws.po` plus `cms/locale/_fragments/aws-*.po`; fragment agreement tests; Babel `check()`; `test_aws_es_style` (Scenario "Spanish register")
- [x] 1.6 Guards: `aws_i18n_lint.py` and `aws_i18n_guard_test.py`, markers on the nine complete files (Scenarios "A new untranslated text", "No translated posted values")
- [x] 1.7 Seed the shared messages in `aws.po` and list the shared sentence blocks and their owner lane
- [x] 1.8 Fix the existing Spanish (fase de análisis, fila, concursantes, user tests, usuario) with the updated pins
- [x] 1.9 Copy the translation guide, glossary and checker into `translation/` of this change with the decisions applied

## 2. Wave 1: L0, L4, L2, L5

- [x] 2.1 L0 chrome and shared script: `base.html`, `login.html`, shared fragments, `aws_utils.js`, `formatting.py`, `handlers/base.py` (Scenarios "A shared script message in Spanish", "A notification read by another admin")
- [x] 2.2 L4 submissions and user tests, with their macros (Scenario "A confirmation in Spanish")
- [x] 2.3 L2 task, including the shared token and limit blocks
- [x] 2.4 L5 users, teams and groups
- [x] 2.5 Wave 1 integration: merge, refresh the catalog, compile, run the checks, one fresh review of the integrated diff

## 3. Wave 2: L1, L3, L6, L7

- [x] 3.1 L1 contest settings and communication, with the quick-answer labels (Scenario "A quick answer from a Spanish page")
- [x] 3.2 L3 dataset
- [x] 3.3 L6 participations, contest tasks, activity and ranking (Scenario "An operation posted from a Spanish page")
- [x] 3.4 L7 system, admins and ranking-group leftovers (empties the named-controls allow-list)
- [x] 3.5 Wave 2 integration: merge, refresh the catalog, compile, run the checks, one fresh review of the integrated diff

## 4. Final

- [x] 4.1 Delete the markers, the English snapshot test and its data; switch `test_every_file_is_complete` on (Scenario "A page group in Spanish")
- [ ] 4.2 Docs: `docs/Localization.rst` (all AWS pages follow the language, the guards and what an upstream sync must do) and the contest-day section, with the Spanish `.po`
- [ ] 4.3 Verification plan: CI mirror, functional tests, pseudo-render report, real AWS smoke, screenshots of 33 pages in both languages
- [ ] 4.4 Whole-branch review, then archive
