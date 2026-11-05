## Context

Observed (read-only, cmsranking/):

- `Ranking.html` (124 lines) has about 17 static texts and already loads `theme.js` first in `<head>`; it is `lang="en"`. The theme select is Spanish (`lang="es"`).
- JavaScript texts (about 25): `Scoreboard.js` (Show tasks / Totals only, table header cells), `Overview.js` (Score, Rank drawn into a Raphael SVG), `UserDetail.js` (Global, the `Ranking - <name>` title, `no submissions`, Yes/No, table headers), `DataStore.js` `update_network_status` (five connection messages, three with an embedded `<a onclick=...>` link), `TimeView.js` labels. `console.*` lines are log only.
- `cmsranking/visibility.py` builds the notice pages (`NOTICE_TEMPLATE`, `NOTICE_THEME` inline script, `HIDDEN_TITLE`, `HIDDEN_MESSAGE`, `FROZEN_LOGIN_TITLE`, `FROZEN_LOGIN_MESSAGE`, the staff and frozen banners, `public_frozen_banner`) with Spanish text, and plain-text 403 bodies.
- `FROZEN_ROUTES` is a default-deny allow-list of first path segments; a hidden group serves only the notice, so the ranking page and its scripts are not served there. `setup.py` package_data covers `cmsranking/static/*.*` but not new subfolders.
- No pluralization and no locale date or number formatting in the ranking scripts.

## Goals / Non-Goals

**Goals:**
- One chosen language across the ranking page and the pages its server builds.
- No new server code path, no new top-level static entry beyond one file.

**Non-Goals:**
- CWS and AWS language (CWS catalog is closed; AWS is the next step of #30).
- More than two languages, a server-side language, date and number formatting, translating names from data or log messages.

## Decisions

All of these are my calls unless marked as the user's.

1. **One script, one JS object (mine).** `cmsranking/static/i18n.js` defines `window.I18n` with the two catalogs as an object (`{ es: {...}, en: {...} }`), `I18n.t(key)` that returns the text of the current language (falling back to English, then to the key), `I18n.lang`, and the early language pick. A single file keeps the allow-list change to one `"i18n.js": "pass"` entry in `FROZEN_ROUTES`, needs no package_data line and no subdirectory. Alternative rejected: a JSON file per language fetched synchronously (new subfolder, package_data, allow-list entry, a blocking request).
2. **Language pick (the user's rule, mine in code).** Saved choice in `localStorage["cms.lang"]` (`es` or `en`, always inside try/catch); else the first of `navigator.languages` / `navigator.language` that starts with `es` gives Spanish; otherwise English. The script sets `document.documentElement.lang` and `data-lang` (`es` or `en`) before the body is parsed, and is loaded in `<head>` right after `theme.js` and before the other scripts, so `DataStore.js`, `Scoreboard.js`, `UserDetail.js`, `Overview.js` and `TimeView.js` can call `I18n.t` at any time.
3. **Static texts by markers (mine).** The HTML marks translatable text with `data-i18n="key"` (text) and `data-i18n-attr="alt:key,title:key,placeholder:key"` (attributes). At `DOMContentLoaded` the script fills them. The HTML keeps the English text, so with no script the page is English as today. A brief flash of English before the fill for a Spanish visitor is accepted (`shortcut:`); the theme script avoids its own flash but the page text is painted before the body scripts could change it.
4. **Switch (mine).** `<select id="language" lang="es">` with `Español` and `English` next to the theme select, wired by `i18n.js` with the same pattern as `theme.js`: it shows the current language, saves the choice and **reloads the page once** (the Raphael overview and the table headers are drawn by scripts at load, and a reload re-renders everything consistently; the ranking reconnects its event stream in a moment). Alternative rejected: live re-render of every widget (touches many scripts for no visible gain).
5. **Dynamic strings keep markup in the catalog (mine).** The three connection messages with links keep their `<a onclick=...>` markup inside the catalog entry (the catalog is static trusted text in our own file); callers use `.html(I18n.t(key))`. No values are interpolated into them. `UserDetail.js` builds the title as `I18n.t("title.user_prefix") + name` and the `no submissions` and Yes/No cells from keys; names from data are concatenated as they are today (the existing code escapes them as before).
6. **Python pages are bilingual in markup (the user's call, mine in form).** Each text carries `<span class="lang-es">…</span><span class="lang-en">…</span>`; the CSS `:root[data-lang="en"] .lang-es{display:none}` and `:root:not([data-lang="en"]) .lang-en{display:none}` shows one. Without JS no `data-lang` is set, so Spanish shows. `<title>` cannot hold spans: it carries the Spanish text and `data-title-en`/the inline snippet swaps it. The inline snippet of the notice pages (`NOTICE_THEME`, today theme only) gains the same language pick as `i18n.js` (kept in one place and tested for identical behavior by running both under node). The banners are inserted into `Ranking.html`, which already has `i18n.js` and `Ranking.css`, so they use the same spans and the CSS rules live in `Ranking.css`.
7. **Safety (mine).** Every interpolated value (group name, times) keeps the escaping it has today and appears in both language spans; no value is placed into the inline script, which stays a constant. The 403 plain-text bodies carry both languages on one line (`Este ranking está oculto. / This ranking is hidden.`) since they are not pages and run no script.
8. **Tests (mine).** A node harness for `i18n.js` like the theme one (browser language rules, saved choice, failing storage, garbage values, the select, the reload on change); a Python test that parses the catalog from the file and asserts the same keys in both languages, no empty value, and that every `data-i18n*` key in `Ranking.html` and every `I18n.t("…")` literal in the scripts exists; bilingual assertions on the Python pages including the escaped group name; `FROZEN_ROUTES` entry; parity test between the early snippet of the notice pages and `i18n.js`'s pick (same inputs, same outputs).

## Risks / Trade-offs

- A string added to the scripts later without a catalog entry shows its key; the completeness test fails first.
- The language pick exists in two places (`i18n.js` and the inline snippet of the notice pages); the parity test guards it.
- The reload on language change loses the open user detail panel; accepted.
- English text flashes for a frame before the fill for Spanish visitors (decision 3).
- The page language of `Ranking.html` (`lang="en"` in the file) is set by the script; the file keeps English as the no-JS fallback.
