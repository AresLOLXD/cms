## Context

The recon (read-only) is in `/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/lanes/lanee/aws-i18n-recon.md`. Observed:

- CWS translations: `cms/locale/locale.py` (`Translation` with the domain hard-coded to `cms` at line 69, `get_translations()` that opens `cms.mo` of every language directory and logs a warning with a traceback when it is missing, `choose_language_code` over Babel's `negotiate_locale`), `ContestWebServer.translations`, `BaseHandler.setup_locale()` (parses `Accept-Language`, reads the `language` cookie, sets `_`, `n_` and `Content-Language`), and `render_params` entries `gettext`, `ngettext`, `translation`, `available_translations`, `cookie_translation`, `automatic_translation`. Jinja's `jinja2.ext.i18n` is enabled in `GLOBAL_ENVIRONMENT`; gettext is injected per request through the render params, not installed in the shared environment.
- AWS has no translation code. `AWS_ENVIRONMENT` is an overlay of the global environment, so the i18n extension is already enabled. `AdminWebServer.__init__` sets `self.jinja2_environment = AWS_ENVIRONMENT`. `BaseHandler._base_render_params` is used by normal pages, error pages and `LoginHandler`. `prepare()` can be skipped before `write_error` (for example a slot timeout). Tests build handlers with `Handler.__new__` and render templates directly without a `gettext`.
- Babel: `setup.cfg` has one domain for `extract_messages`, `init_catalog`, `update_catalog` and `compile_catalog`; `babel_mapping.cfg` does not cover `server/admin`; `build_with_l10n` runs `compile_catalog` at build. `compile_catalog` accepts several domains and skips languages that lack one. `*.mo` files are gitignored and built at install.
- The CWS catalog test (`cmstestsuite/unit_tests/locale/catalog_test.py`) checks no open messages and that extracted msgids are in the template and the Spanish catalog.

## Goals / Non-Goals

**Goals:**
- A working `aws` domain, language choice and selector, proven on the login page, the error pages and the switches.
- The same completeness check as CWS for the new catalog.

**Non-Goals:**
- Wrapping any other page (changes B and C), notifications and jobs (change B).
- A default language per deployment, `es_CL`, a language kept in the database, language in the URL.

## Decisions

All of these are my calls unless marked as the user's.

1. **Separate domain `aws` (the user's).** `Translation` takes a `domain` argument (default `cms`) and `get_translations(domain="cms")` opens `<domain>.mo`. For a domain other than `cms` a language directory without that file is skipped silently, so only languages that really have an AWS catalog are offered; CWS keeps its current behavior (warning per missing file).
2. **Language choice (the user's rule, shape copied from CWS).** `AdminWebServer.translations = get_translations("aws")`. `BaseHandler.setup_locale()` runs at the start of `prepare()` (it needs no database), and in `write_error` when it has not run. Order: valid `aws_language` cookie, else `choose_language_code` over the parsed `Accept-Language`, else English. It sets `self.translation`, `self._`, `self.n_` and the `Content-Language` header. `_base_render_params` adds `translation`, `gettext`, `ngettext`, `available_translations`, `cookie_translation` and `automatic_translation`, which covers pages, error pages and the login page.
3. **Cookie name `aws_language`.** CWS uses `language`; AWS and CWS can share a host on different ports and cookies are not scoped by port, so a distinct name avoids one site reading the other's choice. Path `/`, one year, set by a small inline function (login pages do not load `aws_utils.js`), like `switch_lang` in `cws_utils.js`.
4. **Tests keep working (mine).** `BaseHandler` gets class-level defaults for `translation`, `_` and `n_` (identity), so handlers built with `__new__` work. `AWS_ENVIRONMENT.install_null_translations(newstyle=False)` makes direct template renders and macros imported without context show the English text instead of raising under `StrictUndefined`. The per-request `gettext` in the context shadows it.
5. **Selector (mine).** A `<select id="lang">` in `fragments/theme_switch.html`'s neighbor fragment `fragments/lang_switch.html`, included next to the theme switch in `base.html` and `login.html`, so error pages (which extend the base) and the login page both get it. First option «Automatic (<language>)», then the offered languages named in their own language. Only `es` is offered besides `en`.
6. **Extraction and build.** New `babel_mapping_aws.cfg` (Python `server/admin/**.py`, Jinja `server/admin/templates/**.html` with `trimmed`, `trim_blocks`, `lstrip_blocks`). `cms/locale/aws.pot` and `es/LC_MESSAGES/aws.po` are committed before `setup.cfg` gets `[compile_catalog] domain: cms aws` (the build fails when a domain has no catalog at all). `init_catalog` and `update_catalog` keep the `cms` defaults in `setup.cfg`; the aws runs use `-D aws -i cms/locale/aws.pot` on the command line, documented in `docs/Localization.rst`.
7. **Check generalized (mine).** The CWS catalog test is parametrized by domain: mapping file, template and Spanish catalog. The extraction uses the same keywords and the Jinja extractor named explicitly.
8. **Strings in this change (mine).** Only what an admin sees first: `login.html` (Welcome, Please log in, Username, Password, Login, Reset, Failed to log in), `error.html`, the language selector and the theme switch labels. The theme switch labels become msgids «System», «Light», «Dark» with Spanish «Sistema», «Claro», «Oscuro» and the `lang="es"` on the fragment goes away (the `<html lang>` now carries the page language). This modifies the `color-theme` requirement for AWS (delta in this change). Spanish register: «tú» imperative as in the existing Spanish AWS pages («Elige», «Escribe»).
9. **Notifications and jobs** are language of the admin who starts them (the user's), done in change B.

## Risks / Trade-offs

- `.mo` files are gitignored: an editable install or a checkout that never ran `compile_catalog` has no AWS catalog and AWS silently runs in English; the Docker build compiles them. Documented, as for CWS.
- The shared `GLOBAL_ENVIRONMENT.globals` dict receives the null `gettext` from the overlay; the per-request `gettext` in the render context shadows it, and CWS always passes its own.
- Macros imported without context do not see the per-request `gettext`; nothing in this change puts text in them (change C will handle those imports).
- A visitor whose cookie names a language that later loses its catalog is treated as having no cookie (ignored).
