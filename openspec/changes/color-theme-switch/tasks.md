## 1. Shared mechanism and AWS

- [x] 1.1 Add `cms/server/static/theme.js` (early apply from `localStorage` key `cms.theme` inside try/catch, the `<select id="theme">` wiring) and its tests for the Scenarios of "The theme follows the system unless the user chooses", "The choice is kept in the browser, per server" and "The theme is applied before the first paint" (a JS test with the repo's tooling if there is one; otherwise assertions on the file and the `<head>` placement in the rendered templates)
- [x] 1.2 AWS: tokens and the dark sets in `aws_style.css`, `color-scheme`, the switch in the sidebar, `base.html` loading the script in `<head>`, the Prism dark set, the inline colors and `<style>` blocks moved to tokens, the light panel behind the jqPlot charts; tests for the Scenarios "The switch is on every page" and "Native controls follow the theme" for AWS
- [ ] 1.3 Contrast test module with the pairs for AWS (light and dark); scenario "Contrast in every theme" for AWS

## 2. CWS

- [ ] 2.1 CWS: tokens in `cws_style.css`, the new `cws_dark.css` overlay, the switch in the navbar outside the language condition, the script in `<head>`, chevrons visible in dark, the `#adadad` link fixed; tests for "CWS with a single language", the switch scenarios, "CWS chevrons and RWS ticks" (CWS half)
- [ ] 2.2 Contrast pairs for CWS in the contrast test; scenario "Contrast in every theme" for CWS

## 3. RWS

- [ ] 3.1 RWS: copy of `theme.js` in `cmsranking/static/` listed in `FROZEN_ROUTES`, identical-copy test, tokens and dark sets in `Ranking.css` (score classes and the 8 keyframes included), the switch in the top bar, the footer inline colors, tick/close icons, the light panel behind the charts; the existing `Ranking.css` tests stay green
- [ ] 3.2 `cmsranking/visibility.py`: the hidden notice, staff login and staff banner follow the theme; tests for "The ranking built-in pages" and "Score cells keep their meaning"
- [ ] 3.3 Contrast pairs for RWS (score cells, banners, footer) in the contrast test; scenario "Contrast in every theme" for RWS

## 4. Docs

- [ ] 4.1 Document the switch, where the choice is kept and the light chart panel in the user docs (`docs/`), with the Spanish `.po` and the docs check passing
