# Tasks

## 1. The entry and its address

- [ ] 1.1 `AWSConfig.manual_url` with the default, a handler helper that fills `{language}` and returns empty when hidden, the render parameter, and the `<li>` in `base.html`; the tests in `manual_link_aws_test.py` pass (Scenarios "The home page", "A contest page", "The login and error pages", "The link is safe", "Default address", "Own address", "Language of the page", "Address without a placeholder", "Hidden when empty")
- [ ] 1.2 The two catalog entries («Manual», «Opens in a new tab») in `aws.pot` and `es/aws.po`, regenerated with the documented commands; the AWS i18n guards and the Spanish render test pass (Scenario "A Spanish page")
- [ ] 1.3 Document `manual_url` in `config/cms.sample.toml`; the sample still loads

## 2. Docker

- [ ] 2.1 `CMS_MANUAL_URL` in `docker/generate_config.py` (unset or empty writes nothing, `off` writes an empty address) and in `.env.example`; `docker/test_generate_config.py` passes (Scenarios "Variable unset", "Variable set", "Variable off")
- [ ] 2.2 Document the variable in the table of `docs/docker-deployment.md` and its Spanish `.po`; `docs/check_translations.py` passes

## 3. The manual

- [ ] 3.1 The controls-menu paragraph of `docs/contest-day.md` also names the greeting and Logout, with the Spanish `.po`; the docs checks of `.github/workflows/docs.yml` pass
- [ ] 3.2 Verification: the asyncio pytest group of the admin server, locale and `docker/test_generate_config.py`, pyflakes, `openspec validate aws-manual-link --strict`, the Scenario to test check, a screenshot of the sidebar in both languages

## Workflow follow-up

- Archive the change after the review and verification, as its own commit.
