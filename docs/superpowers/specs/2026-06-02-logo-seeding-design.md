# Ranking: Logo Seeding

**Date:** 2026-06-02
**Status:** Approved

## Overview

Extend the ranking server startup to copy the bundled `logo.png` from the
package to `lib_dir/` automatically, following the same pattern already used
for flag images. This lets any operator customize the logo by replacing the
bundled file, without writing Docker volume mounts or manual copy steps.

---

## Motivation

Flags are seeded to `lib_dir/flags/` at startup via `seed_flags_and_teams`.
The logo has no equivalent mechanism: it falls back to the bundled
`cmsranking/static/img/logo.png` only when nothing exists in `lib_dir/`.
Operators who want a custom logo must configure a volume mount or copy the
file manually after each container start.

The goal is to make logo customization as simple as replacing a file inside
the package — identical to how operators customize flags.

---

## Design

### Behavior

On every `cmsRankingWebServer` startup:

1. `seed_logo(lib_dir)` copies `cmsranking/static/img/logo.png` →
   `lib_dir/logo.png`, overwriting any existing file.
2. The `ImageHandler` already serves `lib_dir/logo.{png,jpg,gif,bmp}` at
   `/logo` and falls back to the bundled default if nothing is found.

To use a temporary logo without modifying the package, place a file at
`lib_dir/logo.{ext}` while the server is running — it is served immediately
but will be overwritten on the next restart.

To make a custom logo permanent, replace `cmsranking/static/img/logo.png`
inside the installed package (same pattern documented for flags).

### Code changes

**`cmsranking/seed.py`** — new public function:

```python
def seed_logo(lib_dir: str) -> None:
    """Copy the bundled logo to lib_dir, overwriting any existing file."""
    src = files("cmsranking") / "static" / "img" / "logo.png"
    dest = os.path.join(lib_dir, "logo.png")
    with open(dest, "wb") as f:
        f.write(src.read_bytes())
```

**`cmsranking/RankingWebServer.py`** — call alongside `seed_flags_and_teams`:

```python
from cmsranking.seed import seed_flags_and_teams, seed_logo
...
seed_flags_and_teams(config.lib_dir, stores["team"])
seed_logo(config.lib_dir)
```

No other code changes required. The `ImageHandler` routing for `/logo` is
unchanged.

### Error handling

If the bundled source file is missing (corrupted installation), `seed_logo`
raises `FileNotFoundError` at startup. This is intentional — a loud failure
is better than silently serving no logo.

---

## Testing

File: `cmstestsuite/unit_tests/cmsranking/test_seed.py`

Two new test cases:

1. `seed_logo` copies the bundled file to `lib_dir/logo.png`.
2. `seed_logo` overwrites a pre-existing `logo.png` in `lib_dir`.

---

## Documentation changes

**`docs/ranking-mexico.md`** — update the "Custom logo" section to describe
the new seeding behavior and the two customization paths (permanent vs.
temporary).

**`docs/RankingWebServer.rst`** — add a "Custom logo" section with the same
content.

---

## Files changed

| File | Change |
|------|--------|
| `cmsranking/seed.py` | Add `seed_logo(lib_dir)` function |
| `cmsranking/RankingWebServer.py` | Import and call `seed_logo` |
| `cmstestsuite/unit_tests/cmsranking/test_seed.py` | Add two test cases |
| `docs/ranking-mexico.md` | Update "Custom logo" section |
| `docs/RankingWebServer.rst` | Add "Custom logo" section |

## Out of scope

- Supporting logo formats other than PNG in the bundled asset (the
  `ImageHandler` already handles `.jpg`, `.gif`, `.bmp` at runtime via
  `lib_dir`).
- A CLI script for logo management (unnecessary given the simple file-replace
  workflow).
- Modifying the Docker compose files (volume mounts are no longer needed).
