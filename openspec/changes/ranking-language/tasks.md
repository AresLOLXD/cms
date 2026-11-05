## 1. Catalog, switch and the ranking page

- [x] 1.1 Add `cmsranking/static/i18n.js` (catalogs, `I18n.t`, language pick from `localStorage["cms.lang"]` / browser language, `lang` and `data-lang` on `<html>`, static text fill by `data-i18n` markers, the `<select id="language">` wiring with a reload on change) and list it in `FROZEN_ROUTES`; node-run tests for the Scenarios "Browser in Spanish", "Browser in English or another language", "Choosing a language", "The choice survives a reload", "Local storage is unavailable", "Theme and language are independent", and the frozen-route Scenario
- [ ] 1.2 Mark the static texts of `Ranking.html`, add the language select next to the theme select and load `i18n.js` after `theme.js`; Scenario "The switch is on the ranking page"
- [ ] 1.3 Move the script texts to the catalog: `DataStore.js` connection messages (markup kept), `Scoreboard.js`, `Overview.js`, `UserDetail.js`, `TimeView.js`; the completeness test for the Scenarios "Both catalogs are complete", "Connection messages keep their links" and "Data names are untouched"

## 2. Pages built by the ranking server

- [ ] 2.1 `cmsranking/visibility.py`: the notice pages, banners, titles and the 403 bodies in both languages; the early snippet picks the language as `i18n.js` does, with the parity test; `Ranking.css` language visibility rules; tests for the Scenarios "The hidden notice in English", "The hidden notice without JavaScript", "The banners follow the language" and "A group name with markup"

## 3. Docs

- [ ] 3.1 One paragraph about the language switch next to the theme section of `docs/contest-day.md`, with the Spanish `.po` and the docs check passing
