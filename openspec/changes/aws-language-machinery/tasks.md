## 1. Catalog machinery

- [x] 1.1 `cms/locale/locale.py`: `Translation(domain=...)` and `get_translations(domain)`, skipping silently the languages without that catalog for a domain other than `cms`; tests for "Offered languages" and "A quiet start", and that the contestant site's loading is unchanged
- [x] 1.2 `babel_mapping_aws.cfg`, `cms/locale/aws.pot` and `cms/locale/es/LC_MESSAGES/aws.po` (login, error, language selector, theme switch entries), then `setup.cfg` `[compile_catalog] domain: cms aws`; `docs/Localization.rst` commands; the catalog test generalized by domain; tests for "Incomplete catalog", "A message missing from the catalog" and "Both catalogs compile"

## 2. Language choice and selector in AWS

- [x] 2.1 `AdminWebServer.translations`, `BaseHandler.setup_locale()` in `prepare()` and guarded in `write_error`, class-level defaults, params in `_base_render_params`, `install_null_translations` in the AWS environment; tests (a handler built with mocks) for the Scenarios "Browser in Spanish", "Browser in English or another language", "The cookie wins" and "A bad cookie is ignored"
- [x] 2.2 `fragments/lang_switch.html` with the cookie setter, included in `base.html` and `login.html`, `<html lang>` from the translation, `Content-Language`; tests for "The selector is on the login and error pages", "Choosing a language" and "Choosing Automatic"

## 3. First translated pages

- [x] 3.1 Wrap `login.html`, `error.html` and the theme switch labels, fill the Spanish catalog, remove the fixed `lang="es"` of the theme switch; tests for "The login page in Spanish", "The error page in Spanish" and "The AWS switch follows the page language"; the existing AWS render and theme tests stay green
