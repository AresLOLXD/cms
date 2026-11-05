## Why

The admin site (AWS) has no translation machinery: its pages are English, and the pages that this fork added are Spanish only, so one admin session shows both languages. Issue #30 asks for AWS in Spanish or English, selectable. Wrapping about 1,100 strings is a large job, so it is split in three changes. This first one builds the machinery and proves it end to end on the few pages that every admin sees first (login, error pages, the switches themselves). The second change moves the Spanish-only pages to translatable text, and the third does the remaining pages.

## What Changes

- AWS gets its own gettext domain `aws` with a Spanish catalog (`es`), separate from the contestant site's catalog, so contestants never load admin strings.
- AWS picks its language per request: a valid saved choice (cookie) wins, otherwise the browser's `Accept-Language` decides between Spanish and English, otherwise English.
- A language selector on every AWS page, including the login and error pages, with an «Automatic» option and the languages that have an AWS catalog.
- Translated now: the login page, the error page, the language selector and the theme switch labels. Every other AWS string stays as it is until the next two changes.
- Building and testing: the build compiles both catalogs; a unit test fails when the AWS Spanish catalog has untranslated or fuzzy messages, or when a message in the code is missing from the catalog (the same check the contestant site has).
- Docs: the localization guide gets the commands for the second catalog.
- Out of scope: wrapping the other pages (next changes), a default language per deployment, `es_CL`, a language stored per admin in the database, language in the URL.

## Capabilities

### New Capabilities
- `aws-language`: which language AWS shows, how it is chosen and kept, where the selector is, and how the catalog is built and checked.

### Modified Capabilities
- `color-theme`: the labels of the theme switch on AWS follow the page language («System / Light / Dark» in English, «Sistema / Claro / Oscuro» in Spanish) instead of being fixed in Spanish.

## Impact

- `cms/locale/locale.py` (a domain parameter), `cms/server/admin/server.py`, `handlers/base.py`, `jinja2_toolbox.py`, templates `base.html`, `login.html`, `error.html`, `fragments/theme_switch.html`.
- New: `babel_mapping_aws.cfg`, `cms/locale/aws.pot`, `cms/locale/es/LC_MESSAGES/aws.po`; `setup.cfg` (`compile_catalog` builds both); `docs/Localization.rst`.
- Tests: the catalog test generalized by domain, a machinery test, existing AWS render tests keep working through null translations.
- No database change, no new dependency.
