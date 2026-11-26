# Proposal

## Why

The fork publishes a manual (cms-omi.readthedocs.io, Spanish and English), but nothing in the Admin Web Server points to it, so a new admin has to know the address. Issue #87 asks for a link on the AWS home page.

## What Changes

- AWS: a sidebar entry "Manual" in the navigation menu, shown on every logged in page (with or without a contest), opening the manual in a new tab, in the language of the page.
- Config: `[admin_web_server] manual_url`, an address with an optional `{language}` placeholder. It defaults to the published manual. An empty value hides the entry, so a deployment with its own copy, or none, can change or remove it.
- Docker: `CMS_MANUAL_URL` writes the option. Unset keeps the default and the word `off` hides the entry (an empty variable means "default", like every other variable of the generator).
- The link text and its new-tab hint go through the AWS catalog (Spanish «Manual»).
- Manual: the controls-menu text in `docs/contest-day.md` also names the greeting and Logout; the option is documented where the manual describes the configuration.

Not built: a card on the Overview, deep links to chapters, a link on the contestant site or the ranking, a copy of the manual served by CMS, checking that the address answers.

## Capabilities

### New Capabilities
- `aws-manual-link`: the AWS navigation entry that opens the manual, where its address comes from, and how an operator changes or hides it.

### Modified Capabilities

None. The `controls-menu` capability is about personal controls only, and a manual link is navigation content.

## Impact

- `cms/conf.py` (`AWSConfig`), `config/cms.sample.toml`, `cms/server/admin/handlers/base.py` (one render parameter), `cms/server/admin/templates/base.html` (one list item).
- `docker/generate_config.py`, `.env.example`, `docs/docker-deployment.md`.
- The AWS catalog `aws.pot` and `es/aws.po`; `docs/contest-day.md` and its Spanish `.po`.
- No database, RPC or JavaScript change. Not deployed by merging; the CMS image is rebuilt on the next release (up.sh option 4).
