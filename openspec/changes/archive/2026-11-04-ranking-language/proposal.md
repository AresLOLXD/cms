## Why

The organizers want every part of CMS available in Spanish or English (issue #30). The contestant site already has a language selector and catalogs. The ranking (RWS) has hard-coded English texts in its page and scripts, and the pages that its server builds in Python (hidden-ranking notice, staff login, banners) are Spanish only, so one ranking shows both languages depending on the page. The ranking is the screen that the public sees, often on a projector or a phone.

## What Changes

- A language switch «Español» / «English» in the ranking top bar, next to the theme switch. The choice is kept in the browser and applies at once; with no choice the page uses the browser language (Spanish if it starts with `es`, English otherwise).
- Every user-visible text of the ranking page (labels, table headers, connection status messages, the global and per-user views, the overview chart labels, alt texts, the page title) comes from a small two-language catalog.
- The pages that the ranking server builds in Python (hidden notice, staff login and failed-login notice, staff and frozen banners) carry both languages and show the one chosen; without JavaScript they show Spanish, as today.
- Out of scope: the contestant site and the admin site (the admin site is the next steps of #30), more than two languages, a server-side language setting, number and date localization, log messages, and names that come from the contest data.

## Capabilities

### New Capabilities
- `ranking-language`: which language the ranking shows, how it is chosen and kept, and which texts it covers.

### Modified Capabilities

## Impact

- New: `cmsranking/static/i18n.js` (catalog, lookup and static-text application), listed in `FROZEN_ROUTES`.
- Edited: `cmsranking/static/Ranking.html`, `DataStore.js`, `Scoreboard.js`, `Overview.js`, `UserDetail.js`, `TimeView.js` (texts through the catalog), `Ranking.css` (language visibility rules for banners), `cmsranking/visibility.py` (bilingual built-in pages).
- No database change, no new Python dependency, no cookie, no server setting.
- Tests: a node-run test of `i18n.js`; a catalog-completeness test; bilingual assertions in the visibility tests; docs paragraph in `docs/contest-day.md` (en and es).
