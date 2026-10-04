# User Manual for the Fork, in English and Spanish — Design Spec

**Date:** 2026-10-02
**Status:** Approved (design, sections 1-3); pending written-spec review
**Issue:** #32 (related: #18, #27, #30)
**When:** docs only, so it does not touch the code frozen for 2026-10-10.
It is built in four phases, each merged directly into `main` once its
review is clean (no PRs). It assumes PR #36
(the contest-day checklist) is merged first.

## Problem

Upstream CMS publishes its manual at cms.readthedocs.io. This fork ships
the same Sphinx sources in `docs/*.rst`, but:

- Nothing is published for the fork, so no manual matches what we run.
- The `.rst` pages describe upstream only. The fork's own pages are loose
  Markdown files outside the Sphinx toctree (`docker-scripts.md`,
  `multi-contest.md`, `subtask-dependencies.md`, `importing-users.md`,
  `contest-day.md`, `rekarel.md`, `ranking-mexico.md`, `cms-loader.md`,
  `migrating-to-multi-contest.md`).
- The build is not reproducible: `docs/requirements.txt` is empty, Sphinx is
  commented out in `pyproject.toml` because it clashed with the pinned
  `babel==2.12.1`, and no CI job builds the docs.
- `docs/superpowers/` (plans and specs) sits in the same folder and is
  internal material.

## Goals

- A published manual for **anyone who uses the fork**, operators and
  developers alike, similar in scope to cms.readthedocs.io.
- **Every page in English and Spanish**, with a language switcher.
- English is the single source; Spanish is a translation that CI keeps
  complete.
- Upstream pages stay close to upstream, so merging upstream into `docs/`
  stays cheap.

## Non-goals

- Translating the AWS, CWS or RWS interfaces (#30).
- Rewriting the upstream pages.
- Publishing `docs/superpowers/`.
- Versioned manuals per release (Read the Docs builds `main`; versions can
  be added later without changing this design).

## Decisions

| Topic | Decision | Rejected |
|-------|----------|----------|
| Audience | Anyone who uses the fork | OMI staff only; layered (ops bilingual, dev English-only) |
| Hosting | Read the Docs | GitHub Pages (hand-made language switcher) |
| Two languages | gettext with `sphinx-intl`: English source, Spanish `.po` files | Two parallel source trees (no way to detect drift) |
| Markup | MyST: `.md` and `.rst` side by side, each page keeps its format | Convert everything to `.rst` or to `.md` |
| Structure | Toctree by task; upstream pages moved, not rewritten | Upstream toctree plus a "This fork" section; full rewrite |
| Theme | Furo | alabaster (current), sphinx-rtd-theme |
| Spanish terms | Follow the CMS interface translation (`cms/locale/es`): "competencia", not "concurso" | Free choice per page |

## Section 1: Build and infrastructure

### Dependencies

`docs/requirements.txt` pins the docs toolchain, separate from the CMS
dependencies: Sphinx 9.1, `myst-parser`, `sphinx-intl`, `furo` (exact pins in
the plan; Sphinx 9.1 was current on 2026-10-02). The docs do
not use `autodoc` directives, so the build never imports `cms` and the
`babel` pin does not matter. Remove the `XXX` Sphinx comment from
`pyproject.toml` and drop `sphinx.ext.autodoc` from `extensions`.

### `docs/conf.py`

- `extensions`: `myst_parser`, `gh_links`.
- `source_suffix`: `.rst` and `.md`.
- `exclude_patterns`: `_build`, `superpowers`, `locale`.
- `locale_dirs = ["locale/"]`, `gettext_compact = False` (one `.po` per
  page).
- `language = "en"`; Read the Docs passes `-D language=<lang>` for each
  project, and a local Spanish build passes `-D language=es`.
- `gettext_location = False`, so a `.po` file changes only when its text
  changes.
- `html_theme = "furo"`.
- The project name and `html_title` say this is the fork's manual; the
  copyright stays "The CMS development team", since the content is
  upstream's.

### `docs/gh_links.py`

Split the targets: `gh_blob` and `gh_tree` point at `AresLOLXD/cms` (branch
`main`), so file links show the fork's code; `gh_download` points at the
fork's `main` archive, since the fork publishes no releases (today it links
to an upstream tag `v1.6.dev0` that does not exist); `gh_issue` keeps
pointing at `cms-dev/cms`, because the issues cited in the upstream pages
are upstream's.

### Read the Docs

- Two projects: the English one, and the Spanish one marked as its
  **translation** (that is how Read the Docs shows the language switcher).
  Both read the same `.readthedocs.yml`.
- `.readthedocs.yml` installs `docs/requirements.txt` and builds
  `docs/conf.py`.
- The user creates both projects on readthedocs.org (Claude cannot). The
  plan writes the steps down.

### CI

A new GitHub Actions job `docs`, run when `docs/**`, `.readthedocs.yml` or
the workflow change:

1. `sphinx-build -W --keep-going -b html docs …` in English: any warning
   fails (broken reference, page outside the toctree).
2. From phase 3 on, the same with `-D language=es`.
3. From phase 3 on, a check that fails when a Spanish `.po` file has an
   empty `msgstr` or a `fuzzy` entry, when its header is marked `fuzzy`
   (Sphinx then ignores the whole file without a warning), when a
   translation drops an inline code span of its source, and when the `.po`
   files are not up to date with the English sources (so a changed English
   paragraph forces a translation update in the same PR).

## Section 2: Content and structure

### Toctree

`docs/index.rst` holds five captioned toctrees:

| Caption | Pages |
|---------|-------|
| Getting started | Introduction · Docker deployment (new) · Docker scripts · Installation · Docker image |
| Preparing a contest | Creating a contest · Configuring a contest · Detailed timing configuration · Task versioning · Importing users · CMS-Loader · Several contests at once · Migrating to multi-contest · Subtask dependencies |
| Running a contest | Contest day · Running CMS · Troubleshooting |
| Reference | Task types · Score types · RankingWebServer · Mexico ranking · Rekarel · External contest formats · Localization |
| Development | Internals · Data model · API |

### New page: Docker deployment

Built from the README's "Deploy with Docker" section (requirements, `.env`,
`./up.sh`, ports, upgrading). The README keeps a short quick start and links
to the manual for the rest.

### Upstream pages

Not rewritten. Where the fork behaves differently, one short note at the top
links to the fork's page. Expected notes:

- Installation: Python 3.12; Docker is the recommended route.
- Running CMS: on Docker the services run under the scripts; most services
  run on asyncio.
- Docker image: the fork's deployment builds this same `Dockerfile` and adds
  a separate Ranking Web Server image and its own (prod) compose file.
- RankingWebServer: ranking groups and hiding/freezing.
- Score types: `depends_on`.

Any other upstream edit is limited to what Sphinx 9.1 needs to build without
warnings, and is listed in the phase's merge commit message.

### Fork pages

- Until phase 3 deletes it, `docs/contest-day.es.md` is built as an orphan
  page (`orphan: true` front matter), outside the toctree.

- CMS-Loader gets a notice that it is being retired in favor of Importing
  users (#18).
- Cross-links between the fork's Markdown pages keep working on GitHub and
  in Sphinx (relative `.md` links, which MyST resolves).

### Spanish

- `sphinx-intl` generates `docs/locale/es/LC_MESSAGES/*.po`.
- **Glossary first:** `docs/locale/es/GLOSSARY.md` fixes the Spanish term
  for each recurring concept. It follows the CMS interface translation in
  `cms/locale/es` ("competencia", "envío", …). Texts of interfaces that are
  only in English (most of AWS) are quoted as they appear on screen.
- Claude drafts every `.po`; the user reviews.
- When the Spanish manual is published, `docs/contest-day.es.md` is deleted:
  its text moves into `contest-day.po`, and the "Versión en español" link in
  `contest-day.md` and in the README points at the published Spanish page.

## Section 3: Phases, verification and risks

### Phases

Each phase is merged directly into `main` after its review, without a PR
(phase 3 may be split by toctree section).

1. **Infrastructure, English only.** Requirements, `conf.py`, MyST, the new
   toctree, Furo, `gh_links`, `superpowers/` excluded, the CI job, the
   `.readthedocs.yml`. **Done when** the English build passes CI with `-W`
   and the user has created the English Read the Docs project and it
   publishes.
2. **Fork content.** The Docker deployment page (and the shorter README),
   the notes on upstream pages, the CMS-Loader notice. **Done when** every
   page is in the toctree and an independent review against the code finds
   no false claim.
3. **Spanish.** Glossary, `.po` drafts for every page, the CI checks for
   Spanish, the Spanish Read the Docs project, `contest-day.es.md` deleted.
   **Done when** the Spanish build passes with `-W`, no `.po` has an empty
   or fuzzy entry, and the user has reviewed the translation.
4. **Close.** The README links to the published manual in both languages;
   #32 is closed.

### Verification

There is no code, so the tests are:

- the `-W` builds in CI (broken references, orphan pages);
- the `.po` completeness check;
- independent reviews of factual accuracy against the code in phases 2
  and 3, as done for the contest-day checklist.

### Risks

- **Old `.rst` under Sphinx 9.1.** Upstream pages may warn on old syntax.
  Phase 1 fixes them with minimal edits and lists each one, since they
  touch upstream files.
- **Translation drift.** Every later change to an English page leaves
  `fuzzy` entries that CI rejects, so every PR that touches the docs must
  update the Spanish too. This is what #32 asks for ("every page must exist
  in both languages"), and it is a permanent cost.
- **Read the Docs needs the user's account.** Phases 1 and 3 cannot be
  closed without it.
- **Upstream merges into `docs/`.** Moving pages between toctrees changes
  only `index.rst`; the notes are a few lines per page. A conflict in a
  translated page leaves `fuzzy` entries that the check flags.
