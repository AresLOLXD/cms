# User Manual for the Fork — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a Sphinx manual of the fork on Read the Docs, in English
and Spanish, with every page of `docs/` in a task-based toctree and CI that
keeps the build warning-free and the Spanish translation complete.

**Architecture:** One Sphinx project in `docs/` reads the upstream `.rst`
pages and the fork's `.md` pages (MyST). English is the source; Spanish is
gettext (`docs/locale/es/LC_MESSAGES/*.po`, managed with `sphinx-intl`). Read
the Docs builds two projects from the same config, the Spanish one marked as
a translation of the English one. A `docs` GitHub Actions workflow builds
both languages with `-W` and runs `docs/check_translations.py`.

**Tech Stack:** Python 3.12, Sphinx 9.1.0, myst-parser 5.1.0, furo
2025.12.19, sphinx-intl 2.4.0 (babel comes with Sphinx), pytest for the
checker's tests, GitHub Actions, Read the Docs.

**Spec:** `docs/superpowers/specs/2026-10-02-fork-manual-design.md`

## Global Constraints

- Docs only: no change to `cms/`, `cmscommon/`, `cmscontrib/`, `cmsranking/`,
  Docker files or `.env.example`. The code is frozen until 2026-10-10.
- Toolchain pins, exactly: `sphinx==9.1.0`, `myst-parser==5.1.0`,
  `furo==2025.12.19`, `sphinx-intl==2.4.0`. The docs build never installs
  `cms` (the `babel==2.12.1` pin of CMS does not apply to it).
- Every build command is `sphinx-build -W --keep-going …`: a warning is a
  failure.
- English is the source language of every page. Spanish lives only in
  `docs/locale/es/LC_MESSAGES/*.po` (after Phase 3; until then the one
  exception is `docs/contest-day.es.md`).
- Spanish terms follow the CMS interface translation in
  `cms/locale/es/LC_MESSAGES/cms.po`: contest = **competencia**, task =
  **problema**, submission = **envío**, subtask = **subtarea**, score =
  **puntaje**, statement = **enunciado**.
- Upstream `.rst` pages: the only allowed edits are the notes listed in
  Task 5 and changes Sphinx needs to build without warnings (each one listed
  in the PR description).
- The fork is public: no hostnames, server paths under `/opt`, IPs, real
  production ports, `.env` contents or credentials in any file.
- Code identifiers, comments and commit messages in English; commits follow
  Conventional Commits and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Each phase is one PR to `main` (Phase 3 may be split, see Task 9). PR
  bodies follow `~/.claude/templates/pull_request_template.md` and end with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- `gh` always with `-R AresLOLXD/cms` (in this checkout it resolves to
  upstream by default).

## Review Focus

1. **A `.po` whose header is marked `#, fuzzy` is silently ignored by
   Sphinx**: the whole page shows in English and the build does not warn.
   `sphinx-intl update` creates every new `.po` that way. → pinned by
   `test_fuzzy_header_is_reported` in Task 7.
2. **A translation that changes a command, path, variable or code span**
   (for example translating `./up.sh` or `CMS_WORKER_COUNT`) still builds
   cleanly but gives operators a wrong instruction. → pinned by
   `test_dropped_code_span_is_reported` in Task 7, and the code-span rule in
   the glossary (Task 8).
3. **Links that build without warnings but are broken in the HTML** (a
   `.md`/`.rst` href left as is, or a link into an excluded page). → the
   `href` scan step in Task 2 and in every translation task.
4. **README anchors that point at the moved Docker section**
   (`#deploy-with-docker`, `#helper-scripts`, `#ports`). → the grep step in
   Task 4.
5. **Fork `.md` pages that stop reading well on GitHub** (MyST-only syntax
   that GitHub shows as raw text). → the rule in Task 2 that the only MyST
   syntax allowed in `.md` files is the `orphan` front matter of
   `contest-day.es.md`, and its check step.

## Files

| Path | Responsibility | Task |
|------|----------------|------|
| `docs/requirements.txt` | Pinned docs toolchain | 1 |
| `docs/conf.py` | Sphinx config: MyST, Furo, gettext, excludes | 1, 8 |
| `docs/index.rst` | Five captioned toctrees | 2 |
| `docs/gh_links.py` | `gh_*` roles: blob/tree/download → fork, issue → upstream | 3 |
| `.readthedocs.yml` | Read the Docs build | 1 |
| `.github/workflows/docs.yml` | CI: builds, checker tests, translation check | 1, 13 |
| `pyproject.toml` | Drop the stale Sphinx comment | 1 |
| `docs/docker-deployment.md` | New page, moved from the README | 4 |
| `README.md` | Short quick start, links to the manual | 4, 14 |
| Five upstream `.rst` pages, `docs/cms-loader.md` | Notes on fork differences | 5 |
| `docs/check_translations.py` | Translation completeness checker | 7 |
| `docs/tests/check_translations_test.py` | Its tests | 7 |
| `docs/locale/es/GLOSSARY.md` | Glossary + how to update the translation | 8 |
| `docs/locale/es/LC_MESSAGES/*.po` | Spanish translation | 8-12 |
| `docs/contest-day.es.md` | Deleted in Task 12 | 2, 12 |

---

## Phase 1 — Infrastructure, English only (PR 1)

### Task 0: Preconditions

- [ ] **Step 1: Check that PR #36 is merged**

Run: `gh -R AresLOLXD/cms pr view 36 --json state --jq .state`
Expected: `MERGED`. If not, stop and tell the user: Phase 1 needs
`docs/contest-day.md` on `main`.

- [ ] **Step 2: Rebase this branch onto main**

The branch `docs/fork-manual` (worktree `.worktrees/fork-manual`) holds the
spec and this plan. Phase 1 continues on it.

```bash
git fetch origin
git rebase origin/main
git log --oneline -3   # spec and plan commits on top of origin/main
```

- [ ] **Step 3: Create the docs virtualenv** (ignored by git through
`.git/info/exclude`; add the line if it is missing)

```bash
grep -qxF '.venv-docs/' "$(git rev-parse --git-common-dir)/info/exclude" \
    || echo '.venv-docs/' >> "$(git rev-parse --git-common-dir)/info/exclude"
uv venv --python 3.12 .venv-docs
```

### Task 1: Toolchain, Sphinx config, Read the Docs and CI

**Files:**
- Modify: `docs/requirements.txt` (empty today), `docs/conf.py`,
  `.readthedocs.yml`, `pyproject.toml:62-64`
- Create: `.github/workflows/docs.yml`

**Interfaces:**
- Produces: `docs/requirements.txt` (installed by CI and Read the Docs);
  the build command `sphinx-build -W --keep-going -b html docs <out>`;
  the workflow `docs` with job `build`, extended in Task 13.

- [ ] **Step 1: Pin the toolchain**

`docs/requirements.txt`:

```text
# Toolchain for the manual. It does not install CMS: the docs use no
# autodoc, so the build never imports it.
sphinx==9.1.0
myst-parser==5.1.0
furo==2025.12.19
sphinx-intl==2.4.0
```

Run: `uv pip install --python .venv-docs/bin/python -r docs/requirements.txt pytest`

- [ ] **Step 2: See that the old config leaves the fork's pages out**

```bash
.venv-docs/bin/sphinx-build -b html docs /tmp/fork-manual-html
test -f /tmp/fork-manual-html/docker-scripts.html && echo built || echo missing
```
Expected: `missing` (the current `conf.py` reads only `.rst`).

Note: `/tmp/fork-manual-*` are throwaway build directories; delete them at
the end of each phase.

- [ ] **Step 3: Update `docs/conf.py`**

Replace these lines (the rest of the file stays):

```python
extensions = ['sphinx.ext.autodoc', 'gh_links']
```
→
```python
extensions = ['myst_parser', 'gh_links']
```

```python
source_suffix = '.rst'
```
→
```python
source_suffix = {'.rst': 'restructuredtext', '.md': 'markdown'}
```

```python
exclude_patterns = ['_build']
```
→
```python
# superpowers/ holds internal specs and plans; locale/ and tests/ are not
# pages.
exclude_patterns = ['_build', 'superpowers', 'locale', 'tests']
```

```python
html_theme = 'alabaster'
```
→
```python
html_theme = 'furo'
```

Replace the whole `html_theme_options = {…}` block (it holds alabaster
options that Furo rejects) with:

```python
html_title = "CMS (OMI fork) " + version
```

Set the project name so the manual says which CMS it is:

```python
project = "CMS"
```
→
```python
project = "CMS (OMI fork)"
```

Append at the end of the file:

```python
# -- Translations ------------------------------------------------------
# English is the source. Read the Docs passes -D language=<lang> for each
# project; a local Spanish build passes -D language=es.
language = 'en'
locale_dirs = ['locale/']
gettext_compact = False
# Without source locations a .po only changes when its text changes.
gettext_location = False
```

- [ ] **Step 4: Fix the three unknown lexers**

Pygments knows neither `env` nor `csv`. In `docs/cms-loader.md` change both
` ```env ` fences (lines 20 and 81) to ` ```bash `; in
`docs/importing-users.md` change the ` ```csv ` fence (line 60) to
` ```text `.

- [ ] **Step 5: Remove the stale Sphinx comment from `pyproject.toml`**

Delete these four lines from the `devel` extra:

```toml

    # Only for building documentation
    # XXX: The version of Sphinx needed to build our documentation
    # is incompatible with the old version of babel we need.
    # "Sphinx>=1.8,<1.9",
```

- [ ] **Step 6: Read the Docs config**

`.readthedocs.yml`:

```yaml
# Read the Docs configuration file for Sphinx projects
# See https://docs.readthedocs.io/en/stable/config-file/v2.html for details

version: 2

build:
  os: ubuntu-24.04
  tools:
    python: "3.12"

sphinx:
  configuration: docs/conf.py
  fail_on_warning: true

python:
  install:
    - requirements: docs/requirements.txt
```

- [ ] **Step 7: CI workflow**

`.github/workflows/docs.yml`:

```yaml
name: docs

on:
  push:
    paths:
      - "docs/**"
      - ".readthedocs.yml"
      - ".github/workflows/docs.yml"
  pull_request:
    paths:
      - "docs/**"
      - ".readthedocs.yml"
      - ".github/workflows/docs.yml"

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install the docs toolchain
        run: pip install -r docs/requirements.txt

      - name: Build the English manual
        run: sphinx-build -W --keep-going -b html docs docs/_build/html/en
```

- [ ] **Step 8: Build; only the "not in any toctree" warnings may remain**

Run: `.venv-docs/bin/sphinx-build -W --keep-going -b html docs /tmp/fork-manual-html 2>&1 | grep WARNING`
Expected: exactly one warning per fork page not yet in the toctree
(`cms-loader`, `contest-day`, `contest-day.es`, `docker-scripts`,
`importing-users`, `migrating-to-multi-contest`, `multi-contest`,
`ranking-mexico`, `rekarel`, `subtask-dependencies`), each "document isn't
included in any toctree" (the message may be localized). Task 2 removes
them. Any other warning: fix it here.

- [ ] **Step 9: Commit**

```bash
git add docs/requirements.txt docs/conf.py docs/cms-loader.md docs/importing-users.md \
    .readthedocs.yml .github/workflows/docs.yml pyproject.toml
git commit -m "build(docs): build the manual with Sphinx 9, MyST and Furo" \
    -m "Pins the docs toolchain apart from the CMS dependencies, reads the fork's Markdown pages, and adds a docs workflow and a Read the Docs config that fail on any warning." \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Task-based toctree

**Files:**
- Modify: `docs/index.rst`, `docs/contest-day.es.md` (front matter only)

**Interfaces:**
- Consumes: the build command from Task 1.
- Produces: the five captions `Getting started`, `Preparing a contest`,
  `Running a contest`, `Reference`, `Development`; Task 4 adds
  `docker-deployment` under `Getting started`.

- [ ] **Step 1: Replace `docs/index.rst`**

```rst
CMS (OMI fork)
==============

This is the manual of the `OMI fork <https://github.com/AresLOLXD/cms>`_ of
the Contest Management System. It is based on the `upstream manual
<https://cms.readthedocs.io>`_ and adds the fork's own features: Docker
deployment, several contests at once, subtask dependencies and the import of
users from the Admin Web Server.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   Introduction
   docker-scripts
   Installation
   Docker image

.. toctree::
   :maxdepth: 2
   :caption: Preparing a contest

   Creating a contest
   Configuring a contest
   Detailed timing configuration
   Task versioning
   importing-users
   cms-loader
   multi-contest
   migrating-to-multi-contest
   subtask-dependencies

.. toctree::
   :maxdepth: 2
   :caption: Running a contest

   contest-day
   Running CMS
   Troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Reference

   Task types
   Score types
   RankingWebServer
   ranking-mexico
   rekarel
   External contest formats
   Localization

.. toctree::
   :maxdepth: 2
   :caption: Development

   Internals
   Data model
   API
```

- [ ] **Step 2: Mark the Spanish runbook as an orphan page**

`docs/contest-day.es.md` stays outside the toctree until Task 12 deletes it.
Add this front matter as its first lines (GitHub shows it as a small table;
it is the only MyST-specific syntax allowed in the `.md` pages):

```markdown
---
orphan: true
---

```

- [ ] **Step 3: Build with `-W`**

Run: `.venv-docs/bin/sphinx-build -W --keep-going -b html docs /tmp/fork-manual-html`
Expected: exit 0, no warnings.

- [ ] **Step 4: Scan the HTML for links left as source files**

MyST turns `[x](page.md)` into a link to `page.html`. A link it could not
resolve stays as `page.md` without always warning.

Run: `grep -rhoE 'href="[^"#]*\.(md|rst)(#[^"]*)?"' /tmp/fork-manual-html --include='*.html' | sort | uniq -c`
Expected: no output. If a link appears, fix its target in the source page.

- [ ] **Step 5: Check that the fork pages use no other MyST-only syntax**

Run: `grep -nE '^(:::|```\{)|\{(doc|ref|gh_[a-z]+)\}`' docs/*.md`
Expected: no output (directives and roles would show as raw text on
GitHub).

- [ ] **Step 6: Commit**

```bash
git add docs/index.rst docs/contest-day.es.md
git commit -m "docs: organize the manual by task and include the fork's pages" \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Point the GitHub roles at the fork

**Files:**
- Modify: `docs/gh_links.py`

**Interfaces:**
- Produces: `:gh_blob:` and `:gh_tree:` link to
  `https://github.com/AresLOLXD/cms/{blob,tree}/main/<path>`;
  `:gh_download:` to `https://github.com/AresLOLXD/cms/archive/refs/heads/main.tar.gz`;
  `:gh_issue:` keeps `https://github.com/cms-dev/cms/issues/<n>`.

- [ ] **Step 1: Write the failing check**

Run: `grep -c 'github.com/AresLOLXD/cms/blob/main/Dockerfile' /tmp/fork-manual-html/Docker\ image.html`
Expected: `0` (today the link goes to `cms-dev/cms/blob/v1.6.dev0/…`, a tag
that does not exist).

- [ ] **Step 2: Change the URLs**

In `docs/gh_links.py`, add after the imports:

```python
# File links show the fork's code. Issue numbers in the upstream pages
# refer to upstream's tracker, so they keep pointing there.
FORK_URL = 'https://github.com/AresLOLXD/cms'
UPSTREAM_URL = 'https://github.com/cms-dev/cms'
```

and change the four URL lines:

```python
    full_url = 'https://github.com/cms-dev/cms/issues/%s' % part
```
→
```python
    full_url = '%s/issues/%s' % (UPSTREAM_URL, part)
```

```python
        full_url = 'https://github.com/cms-dev/cms/releases/download/v%(ver)s/v%(ver)s.tar.gz' % {"ver": app.config.release}
```
→
```python
        # The fork publishes no releases: download the main branch.
        full_url = '%s/archive/refs/heads/main.tar.gz' % FORK_URL
```

```python
        full_url = 'https://github.com/cms-dev/cms/tree/v%s/%s' % (app.config.release, part)
```
→
```python
        full_url = '%s/tree/main/%s' % (FORK_URL, part)
```

```python
        full_url = 'https://github.com/cms-dev/cms/blob/v%s/%s' % (app.config.release, part)
```
→
```python
        full_url = '%s/blob/main/%s' % (FORK_URL, part)
```

- [ ] **Step 3: Build and check every role**

```bash
.venv-docs/bin/sphinx-build -W --keep-going -E -b html docs /tmp/fork-manual-html
grep -c 'AresLOLXD/cms/blob/main/Dockerfile' "/tmp/fork-manual-html/Docker image.html"   # >= 1
grep -c 'AresLOLXD/cms/archive/refs/heads/main.tar.gz' /tmp/fork-manual-html/Installation.html   # 1
grep -c 'cms-dev/cms/issues/61' "/tmp/fork-manual-html/Configuring a contest.html"   # 1
grep -rlc 'cms-dev/cms/\(blob\|tree\)/v' /tmp/fork-manual-html --include='*.html'   # no output
```

- [ ] **Step 4: Commit**

```bash
git add docs/gh_links.py
git commit -m "docs: link the manual's file references to the fork" \
    -m "gh_blob, gh_tree and gh_download pointed at upstream tags such as v1.6.dev0, which do not exist. Issue links keep pointing at upstream's tracker." \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3b: Read the Docs (English) and PR 1 — needs the user

- [ ] **Step 1: Push and open PR 1**

```bash
git push -u origin HEAD:docs/fork-manual
gh -R AresLOLXD/cms pr create --base main --head docs/fork-manual \
    --title "build(docs): publish the fork's manual (phase 1: infrastructure)" --body-file <body>
```

The body follows the template; "Cómo probarlo" lists the build command and
the `href` scan; "Qué puede romper" says it changes no code.

- [ ] **Step 2: Ask the user to create the English project** (Claude cannot)

Give the user these steps, in Spanish:
1. readthedocs.org → sign in with GitHub → **Add project** → import
   `AresLOLXD/cms`.
2. Name/slug: `cms-fork` if free (otherwise one they choose). Language:
   **English**. Default branch: `main`.
3. Admin → **Pull request builds**: on (previews per PR).
4. Trigger a build of the PR branch and send back the URL.

Record the slug: later tasks use `https://<slug>.readthedocs.io/en/latest/`.

- [ ] **Step 3: Done when** CI `docs` is green on PR 1, the Read the Docs
build of the PR is green, and the user has merged PR 1.

---

## Phase 2 — Fork content (PR 2)

Branch `docs/manual-content` from the updated `origin/main`, in a new
worktree `.worktrees/manual-content`.

### Task 4: Docker deployment page

**Files:**
- Create: `docs/docker-deployment.md`
- Modify: `README.md`, `docs/index.rst`

**Interfaces:**
- Produces: the page `docker-deployment` under `Getting started`, after
  `Introduction`.

- [ ] **Step 1: Move the README sections into the new page**

Move everything from `## Deploy with Docker` up to (not including)
`## Upstream project` — that is "Deploy with Docker" and "Helper scripts" —
into `docs/docker-deployment.md`, with these changes:
- `## Deploy with Docker` becomes the page title `# Docker deployment`;
  its `###` subsections become `##`. `## Helper scripts` stays `##`.
- Links `docs/<page>.md` become `<page>.md`.
- Links to files outside `docs/` (for example `.env.example`) become
  `https://github.com/AresLOLXD/cms/blob/main/<path>`.

- [ ] **Step 2: Leave a pointer in the README**

In place of the moved sections:

```markdown
## Deploy with Docker

The full guide — requirements, configuration, ports, upgrades, common
operations and the helper scripts — is in
[docs/docker-deployment.md](docs/docker-deployment.md) and in the
[manual](https://<slug>.readthedocs.io/en/latest/docker-deployment.html).
```

(`<slug>` is the one recorded in Task 3b.)

Fix the README's own anchors: the Quick Start line "See [Deploy with
Docker](#deploy-with-docker) below." keeps working (the heading stays); the
Features row "Docker deployment" links to `docs/docker-deployment.md`.

- [ ] **Step 3: Add the page to the toctree**

In `docs/index.rst`, under `:caption: Getting started`, insert
`docker-deployment` after `Introduction`.

- [ ] **Step 4: Check anchors, links and build**

```bash
grep -nE '\(#(helper-scripts|ports|what-you-need-first|step-[0-9]|common-operations|troubleshooting)\)' README.md   # no output
grep -nE '\]\(docs/' docs/docker-deployment.md    # no output
.venv-docs/bin/sphinx-build -W --keep-going -b html docs /tmp/fork-manual-html
grep -rhoE 'href="[^"#]*\.(md|rst)(#[^"]*)?"' /tmp/fork-manual-html --include='*.html'   # no output
```

- [ ] **Step 5: Commit**

```bash
git add README.md docs/docker-deployment.md docs/index.rst
git commit -m "docs: move the Docker deployment guide from the README into the manual" \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Notes on the upstream pages and the CMS-Loader notice

**Files:**
- Modify: `docs/Installation.rst`, `docs/Running CMS.rst`,
  `docs/Docker image.rst`, `docs/RankingWebServer.rst`,
  `docs/Score types.rst`, `docs/cms-loader.md`

- [ ] **Step 1: Add one note under each title**

Each note goes right after the title's underline and a blank line, followed
by a blank line. Exact texts:

`Installation.rst`:
```rst
.. note::

   This fork runs on Python 3.12, and the recommended way to run it is the
   :doc:`Docker deployment <docker-deployment>`. This page describes the
   manual installation inherited from upstream CMS.
```

`Running CMS.rst`:
```rst
.. note::

   On a Docker deployment the services are started and supervised by the
   :doc:`helper scripts <docker-scripts>`; you do not run them by hand. In
   this fork most services run on Python's asyncio instead of gevent; the
   Worker and the Ranking Web Server still use gevent.
```

`Docker image.rst`:
```rst
.. note::

   This page describes upstream's Docker image for development and testing.
   To run contests with this fork, use the :doc:`Docker deployment
   <docker-deployment>`, which has its own images and compose files.
```

`RankingWebServer.rst`:
```rst
.. note::

   In this fork one Ranking Web Server can serve several scoreboards, one
   per ranking group, and each can be hidden or frozen on a schedule. See
   :doc:`multi-contest`.
```

`Score types.rst`:
```rst
.. note::

   In this fork a subtask can depend on other subtasks: if a dependency
   scores 0, the subtask is worth 0 and is not graded. See
   :doc:`subtask-dependencies`.
```

Before writing each note, check its claim against the code (Task 6 reviews
them again): Python version in `.python-version`; gevent users in
`cms/service/Worker.py` and `cmsranking/RankingWebServer.py`; ranking groups
in `cms/db/rankinggroup.py`; `depends_on` in `cms/grading/subtaskdag.py`.

- [ ] **Step 2: Point the CMS-Loader notice at the retirement issue**

In `docs/cms-loader.md`, replace the opening quote:

```markdown
> AWS can now import users and participations itself; see
> [importing-users.md](importing-users.md). CMS-Loader will be removed
> after 2026-10-10.
```
→
```markdown
> **Being retired.** The Admin Web Server now imports users and
> participations itself; see [importing-users.md](importing-users.md).
> CMS-Loader will be removed after 2026-10-10
> ([#18](https://github.com/AresLOLXD/cms/issues/18)).
```

- [ ] **Step 3: Build and scan**

```bash
.venv-docs/bin/sphinx-build -W --keep-going -E -b html docs /tmp/fork-manual-html
grep -c 'admonition note' /tmp/fork-manual-html/Installation.html "/tmp/fork-manual-html/Running CMS.html" \
    "/tmp/fork-manual-html/Docker image.html" /tmp/fork-manual-html/RankingWebServer.html "/tmp/fork-manual-html/Score types.html"
```
Expected: build exits 0; each file reports at least 1.

- [ ] **Step 4: Commit**

```bash
git add "docs/Installation.rst" "docs/Running CMS.rst" "docs/Docker image.rst" \
    docs/RankingWebServer.rst "docs/Score types.rst" docs/cms-loader.md
git commit -m "docs: flag where the fork differs on the upstream pages" \
    -m "Refs #32, #18" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: Accuracy review and PR 2

- [ ] **Step 1: Independent review against the code**

Dispatch a reviewer (not the implementer's agent type) with: the diff of
PR 2, the instruction to verify every factual claim in
`docs/docker-deployment.md` (it was README text: check commands, ports,
variables against `docker/`, `.env.example`, the `*.sh` scripts) and in the
five notes, citing file:line, and to list wrong claims first. Fix every
confirmed finding and commit with `docs: …` messages.

- [ ] **Step 2: Push and open PR 2** (same template; "Cómo probarlo": build
command, `href` scan, README anchor grep). **Done when** CI and the Read the
Docs PR build are green and the user merges it.

---

## Phase 3 — Spanish (PR 3, may be split by toctree section)

Branch `docs/manual-es` from the updated `origin/main`, in a new worktree.

### Task 7: Translation checker (TDD)

**Files:**
- Create: `docs/check_translations.py`, `docs/tests/check_translations_test.py`

**Interfaces:**
- Produces: `check(pot_dir: Path, po_dir: Path) -> list[str]` (empty list
  = complete) and the CLI `python docs/check_translations.py POT_DIR PO_DIR`
  (exit 0 when complete, 1 otherwise, one problem per line on stdout).

- [ ] **Step 1: Write the failing tests**

`docs/tests/check_translations_test.py`:

```python
"""Tests for docs/check_translations.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_translations import check  # noqa: E402

HEADER = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=utf-8\\n"
"Language: es\\n"

'''
FUZZY_HEADER = "#, fuzzy\n" + HEADER

TEMPLATE = HEADER + '''msgid "How it works"
msgstr ""

msgid "Run `./up.sh` and set ``CMS_WORKER_COUNT``."
msgstr ""
'''

GOOD = HEADER + '''msgid "How it works"
msgstr "Cómo funciona"

msgid "Run `./up.sh` and set ``CMS_WORKER_COUNT``."
msgstr "Ejecuta `./up.sh` y define ``CMS_WORKER_COUNT``."
'''


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def run(tmp_path: Path, po_text: str | None, pot_text: str = TEMPLATE) -> list[str]:
    pot_dir, po_dir = tmp_path / "pot", tmp_path / "po"
    write(pot_dir, "page.pot", pot_text)
    po_dir.mkdir(parents=True, exist_ok=True)
    if po_text is not None:
        write(po_dir, "page.po", po_text)
    return check(pot_dir, po_dir)


def test_complete_translation_passes(tmp_path):
    assert run(tmp_path, GOOD) == []


def test_missing_po_file_is_reported(tmp_path):
    problems = run(tmp_path, None)
    assert len(problems) == 1 and "no .po file" in problems[0]


def test_po_without_page_is_reported(tmp_path):
    write(tmp_path / "po", "gone.po", GOOD)
    problems = run(tmp_path, GOOD)
    assert len(problems) == 1 and "gone.po" in problems[0]


def test_untranslated_message_is_reported(tmp_path):
    po = GOOD.replace('msgstr "Cómo funciona"', 'msgstr ""')
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "not translated" in problems[0]


def test_fuzzy_message_is_reported(tmp_path):
    po = GOOD.replace('msgid "How it works"', '#, fuzzy\nmsgid "How it works"')
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "fuzzy" in problems[0]


def test_fuzzy_header_is_reported(tmp_path):
    # Sphinx ignores a whole catalog whose header is fuzzy, silently.
    po = GOOD.replace(HEADER, FUZZY_HEADER, 1)
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "header" in problems[0]


def test_message_missing_from_po_is_reported(tmp_path):
    pot = TEMPLATE + '\nmsgid "New paragraph"\nmsgstr ""\n'
    problems = run(tmp_path, GOOD, pot)
    assert len(problems) == 1 and "missing" in problems[0]


def test_dropped_code_span_is_reported(tmp_path):
    po = GOOD.replace("``CMS_WORKER_COUNT``", "``CMS_NUMERO_DE_WORKERS``")
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "CMS_WORKER_COUNT" in problems[0]


def test_link_and_role_text_may_be_translated(tmp_path):
    pot = HEADER + (
        'msgid "See :doc:`task types <Task types>` and '
        '`USACO <http://usaco.org/>`_."\nmsgstr ""\n')
    po = HEADER + (
        'msgid "See :doc:`task types <Task types>` and '
        '`USACO <http://usaco.org/>`_."\n'
        'msgstr "Ver :doc:`tipos de problema <Task types>` y '
        '`USACO <http://usaco.org/>`_."\n')
    assert run(tmp_path, po, pot) == []


def test_code_spans_next_to_an_underscore_are_code(tmp_path):
    # "` or `_" must not be taken for a link: `-` and `_` are both code.
    pot = HEADER + 'msgid "Use `-` or `_` in `olim`."\nmsgstr ""\n'
    po = HEADER + ('msgid "Use `-` or `_` in `olim`."\n'
                   'msgstr "Usa `-` o `_` en `olim`."\n')
    assert run(tmp_path, po, pot) == []
    po_without = po.replace("`_`", "guion bajo")
    problems = run(tmp_path, po_without, pot)
    assert len(problems) == 1 and "`_`" in problems[0]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv-docs/bin/python -m pytest docs/tests -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'check_translations'`.

- [ ] **Step 3: Write the checker**

`docs/check_translations.py`:

```python
#!/usr/bin/env python3

"""Check that a translation of the manual is complete.

Usage: check_translations.py POT_DIR PO_DIR

POT_DIR holds the templates written by `sphinx-build -b gettext`; PO_DIR
holds one language's catalogs (docs/locale/<lang>/LC_MESSAGES). Print every
problem and exit 1 if a page has no catalog, a catalog has no page, a
message is missing, untranslated or fuzzy, a catalog header is fuzzy (Sphinx
then ignores the whole catalog without a warning), or a translation lost an
inline code span of its source.

"""

import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

# Roles (:doc:`x`, {doc}`x`) and reST links (`text <url>`_): their text
# may be translated, so they are removed before looking for code. A link's
# text never starts with a space, which keeps "`-` or `_`" two code spans.
ROLE_OR_LINK = re.compile(
    r"(?::[\w:+.-]+:|\{[\w:+.-]+\})`[^`]*`|(?<!`)`[^`\s][^`]*`__?")
# Inline code in reStructuredText (``x``) and Markdown (`x`).
CODE_SPAN = re.compile(r"``[^`]+``|`[^`]+`")


def read_catalog(path: Path) -> Catalog:
    """Read a .po or .pot file."""
    with path.open("rb") as f:
        return read_po(f)


def short(text: str, width: int = 60) -> str:
    """Return the start of a message, for error lines."""
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def catalogs(directory: Path, suffix: str) -> dict[Path, Path]:
    """Map each catalog's path without suffix to its file."""
    return {path.relative_to(directory).with_suffix(""): path
            for path in directory.rglob("*" + suffix)}


def check(pot_dir: Path, po_dir: Path) -> list[str]:
    """Return the problems of the translation in po_dir.

    pot_dir: the templates of the current English sources.
    po_dir: the catalogs of one language.

    return: one line per problem; empty when the translation is complete.

    """
    problems = []
    templates = catalogs(pot_dir, ".pot")
    translations = catalogs(po_dir, ".po")
    for name in sorted(templates.keys() - translations.keys()):
        problems.append(f"{name}.po: no .po file (run sphinx-intl update)")
    for name in sorted(translations.keys() - templates.keys()):
        problems.append(f"{name}.po: no page produces it (delete it)")
    for name in sorted(templates.keys() & translations.keys()):
        catalog = read_catalog(translations[name])
        if catalog.fuzzy:
            problems.append(f"{name}.po: the header is marked fuzzy, so "
                            f"Sphinx ignores the whole catalog")
        for source in read_catalog(templates[name]):
            if not source.id:
                continue
            label = f"{name}.po: {short(source.id)!r}"
            message = catalog.get(source.id, source.context)
            if message is None:
                problems.append(f"{label}: missing (run sphinx-intl update)")
            elif not message.string:
                problems.append(f"{label}: not translated")
            elif message.fuzzy:
                problems.append(f"{label}: fuzzy")
            else:
                code = CODE_SPAN.findall(ROLE_OR_LINK.sub(" ", source.id))
                for span in code:
                    if span not in message.string:
                        problems.append(
                            f"{label}: the translation lacks {span}")
    return problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    problems = check(Path(sys.argv[1]), Path(sys.argv[2]))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv-docs/bin/python -m pytest docs/tests -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/check_translations.py docs/tests/check_translations_test.py
git commit -m "test(docs): add a checker that the manual's translation is complete" \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: Spanish catalogs and glossary

**Files:**
- Create: `docs/locale/es/GLOSSARY.md`, `docs/locale/es/LC_MESSAGES/*.po`
  (generated)

**Interfaces:**
- Consumes: `check()` from Task 7.
- Produces: one `.po` per page (empty translations), the glossary that
  Tasks 9-12 follow, and the update commands below (used by every later
  docs PR).

- [ ] **Step 1: Generate the catalogs**

```bash
rm -rf /tmp/fork-manual-pot
.venv-docs/bin/sphinx-build -W --keep-going -b gettext docs /tmp/fork-manual-pot
.venv-docs/bin/sphinx-intl update -p /tmp/fork-manual-pot -l es -d docs/locale
ls docs/locale/es/LC_MESSAGES/*.po | wc -l      # one per page (27 with docker-deployment)
```

Note `-d docs/locale`: without it, `sphinx-intl` writes to `locales/`, which
Sphinx does not read.

- [ ] **Step 2: See the checker fail on the empty catalogs**

Run: `.venv-docs/bin/python docs/check_translations.py /tmp/fork-manual-pot docs/locale/es/LC_MESSAGES | tail -3; echo "exit $?"`
Expected: about 1,400 "not translated" lines plus one "header is marked
fuzzy" per file; exit 1.

- [ ] **Step 3: Write `docs/locale/es/GLOSSARY.md`**

```markdown
# Spanish translation of the manual

The manual is written in English. The Spanish version is in the `.po` files
of `LC_MESSAGES/`, one per page.

## After changing an English page

    sphinx-build -W --keep-going -b gettext docs /tmp/pot
    sphinx-intl update -p /tmp/pot -l es -d docs/locale
    python docs/check_translations.py /tmp/pot docs/locale/es/LC_MESSAGES

Translate every entry the checker lists, then delete the `#, fuzzy` line
above any entry you have checked. A new `.po` file also starts with
`#, fuzzy` in its header: delete that line too, or Sphinx ignores the whole
file. Build the Spanish manual with
`sphinx-build -W --keep-going -b html -D language=es docs /tmp/html-es`.

## Rules

- Keep verbatim: everything between backticks (commands, paths, variables,
  code), URLs, the targets of links and roles (`:doc:`, `{doc}`), and the
  names of reST/MyST constructs. Translate the visible text of links and
  roles.
- Texts of an interface quote it as the interface shows it. The contestant
  interface is translated (use its Spanish text, from
  `cms/locale/es/LC_MESSAGES/cms.po`); the Admin Web Server and most of the
  Ranking Web Server are in English or in their own Spanish labels
  ("Ocultar ahora"): quote them unchanged.
- Address the reader as "tú".

## Terms

| English | Spanish |
|---------|---------|
| contest | competencia |
| task | problema |
| submission | envío |
| user test | prueba de usuario |
| subtask | subtarea |
| testcase | caso de prueba |
| score | puntaje |
| statement | enunciado |
| attachment | adjunto |
| token | token |
| contestant | concursante |
| scoreboard, ranking | ranking |
| ranking group | grupo de ranking |
| worker | worker |
| queue | cola |
| evaluation | evaluación |
| compilation | compilación |
| two-phase screening | evaluación en dos fases |
| dependency | dependencia |
| deployment | despliegue |
| backup | respaldo |
| staff | staff |
| freeze / unfreeze | congelar / descongelar |
| hide / show | ocultar / mostrar |
| analysis mode | fase de análisis |
| Admin Web Server, Contest Web Server, Ranking Web Server, Evaluation Service, … | unchanged |
```

- [ ] **Step 4: Commit the catalogs and the glossary**

```bash
git add docs/locale/es
git commit -m "docs(es): add the Spanish catalogs of the manual and a glossary" \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Tasks 9-12: Translate, one toctree section per task

Each task translates every message of its files, following
`docs/locale/es/GLOSSARY.md`, and removes the header `#, fuzzy` line of each
of its files. Same steps for each task; only the file list changes.

| Task | Files (`docs/locale/es/LC_MESSAGES/…`) |
|------|-----------------------------------------|
| 9 | `index.po`, `Introduction.po`, `docker-deployment.po`, `docker-scripts.po`, `Installation.po`, `Docker image.po` |
| 10 | `Creating a contest.po`, `Configuring a contest.po`, `Detailed timing configuration.po`, `Task versioning.po`, `importing-users.po`, `cms-loader.po`, `multi-contest.po`, `migrating-to-multi-contest.po`, `subtask-dependencies.po` |
| 11 | `Running CMS.po`, `Troubleshooting.po`, `Task types.po`, `Score types.po`, `RankingWebServer.po`, `ranking-mexico.po`, `rekarel.po`, `External contest formats.po`, `Localization.po`, `Internals.po`, `Data model.po`, `API.po` |
| 12 | `contest-day.po`, plus deleting `docs/contest-day.es.md` |

Task 10 is the largest (about 600 messages): its implementer may commit per
file.

- [ ] **Step 1: Translate the task's files**

Edit each `msgstr`. For `contest-day.po` (Task 12), reuse the wording of
`docs/contest-day.es.md` but apply the glossary ("competencia", not
"concurso"); then `git rm docs/contest-day.es.md`, remove the line
`[Versión en español](contest-day.es.md)` and the blank line after it from
`docs/contest-day.md`, regenerate the catalogs (Task 8, Step 1) and
translate any message that changed.

- [ ] **Step 2: The checker reports nothing for the task's files**

Run: `.venv-docs/bin/python docs/check_translations.py /tmp/fork-manual-pot docs/locale/es/LC_MESSAGES | grep -F -e '<file 1>' -e '<file 2>' …`
(one `-e` per file of the task, without `.po`)
Expected: no output.

- [ ] **Step 3: Build the Spanish manual and compare links with English**

```bash
.venv-docs/bin/sphinx-build -W --keep-going -E -b html -D language=es docs /tmp/fork-manual-html-es
.venv-docs/bin/sphinx-build -W --keep-going -E -b html docs /tmp/fork-manual-html
for f in /tmp/fork-manual-html/*.html; do
  b=$(basename "$f"); e=$(grep -o 'href="' "$f" | wc -l); s=$(grep -o 'href="' "/tmp/fork-manual-html-es/$b" | wc -l)
  [ "$e" = "$s" ] || echo "$b: $e links in English, $s in Spanish"
done
grep -rhoE 'href="[^"#]*\.(md|rst)(#[^"]*)?"' /tmp/fork-manual-html-es --include='*.html'
```
Expected: both builds exit 0; no line printed by the loop for the task's
pages; the last grep prints nothing.

- [ ] **Step 4: Read three translated pages in the browser** (open
`/tmp/fork-manual-html-es/<page>.html`): no English paragraph left, code and
commands unchanged.

- [ ] **Step 5: Commit**

```bash
git add docs/locale/es/LC_MESSAGES
git commit -m "docs(es): translate <section caption> into Spanish" \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
(Task 12 also adds `docs/contest-day.md` and the deletion.)

### Task 13: Spanish in CI

**Files:**
- Modify: `.github/workflows/docs.yml`

- [ ] **Step 1: Add the steps after "Build the English manual"**

```yaml
      - name: Test the translation checker
        run: |
          pip install pytest
          python -m pytest docs/tests -q

      - name: Check the Spanish translation
        run: |
          sphinx-build -W --keep-going -b gettext docs docs/_build/gettext
          python docs/check_translations.py docs/_build/gettext docs/locale/es/LC_MESSAGES

      - name: Build the Spanish manual
        run: sphinx-build -W --keep-going -b html -D language=es docs docs/_build/html/es
```

- [ ] **Step 2: Prove the check fails on a stale translation**

Temporarily change one English sentence in `docs/rekarel.md`, push the
branch, and confirm the `docs` job fails at "Check the Spanish translation"
listing that message. Revert the change (`git revert` of the temporary
commit) and confirm the job is green.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "ci(docs): build the Spanish manual and check its translation" \
    -m "Refs #32" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 13b: Bilingual review, PR 3 and the Spanish project — needs the user

- [ ] **Step 1:** Dispatch an independent bilingual reviewer over the
`.po` diff: wrong meaning, glossary violations, untranslated UI quotes that
should follow `cms.po`. Fix confirmed findings.
- [ ] **Step 2:** Push and open PR 3 (or one PR per finished section).
- [ ] **Step 3:** Ask the user (in Spanish) to create the Spanish project on
readthedocs.org: import `AresLOLXD/cms` again, slug `<slug>-es`, language
**Spanish**; then in the English project, Admin → **Translations** → add
`<slug>-es`. The switcher then shows at
`https://<slug>.readthedocs.io/es/latest/`.
- [ ] **Step 4: Done when** CI is green, the user has reviewed the
translation and merged PR 3, and the Spanish site is live.

---

## Phase 4 — Close (PR 4)

### Task 14: Link the published manual

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** Under the README title, add:

```markdown
**Manual:** [English](https://<slug>.readthedocs.io/en/latest/) ·
[Español](https://<slug>.readthedocs.io/es/latest/)
```

- [ ] **Step 2:** In the Features table, the "Contest-day checklist" row
links `[docs/contest-day.md](docs/contest-day.md)` and
`[español](https://<slug>.readthedocs.io/es/latest/contest-day.html)` (the
`.es.md` file no longer exists).

- [ ] **Step 3:** `grep -n 'contest-day.es.md' -r README.md docs/` prints
nothing; open both URLs and check they load.

- [ ] **Step 4:** Commit (`docs: link the published manual from the
README`, `Closes #32`), push, open PR 4; after the merge, comment on #32
with the two URLs.
