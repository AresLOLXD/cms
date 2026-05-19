# Ranking: Custom Logo Documentation & Real Mexican State Flags

**Date:** 2026-05-19
**Status:** Approved

## Overview

Two improvements to the ranking web server's visual assets:

1. **Custom logo** — document the existing override mechanism so operators know how to replace the default CMS logo.
2. **Real Mexican state flags** — replace the 32 placeholder solid-color images (40×25 px) with actual state flag images downloaded from Wikimedia Commons, resized to 160×100 px and committed as bundled assets.

---

## Feature 1: Custom Logo (Documentation Only)

### How it already works

`RankingWebServer` routes `GET /logo` through an `ImageHandler` that looks for:

1. `{lib_dir}/logo.{png,jpg,gif,bmp}` — custom logo placed by the operator
2. Falls back to `cmsranking/static/img/logo.png` — the bundled default

No code changes are required.

### Documentation addition

Add a section to `docs/RankingWebServer.rst` explaining:

> To use a custom logo, place a `logo.png` (or `.jpg`, `.gif`, `.bmp`) directly in `lib_dir` (default: `$VIRTUAL_ENV/lib/ranking/`). The server will serve it automatically at `/logo`. Remove the file to revert to the bundled default.

---

## Feature 2: Real Mexican State Flags

### Current state

`cmsranking/flags/` contains 32 PNG files (one per state, e.g. `JAL.png`) that are solid-color rectangles at 40×25 px. These are placeholder images with no visual content.

### Target state

Each file replaced with the actual state flag image at **160×100 px**, sourced from Wikimedia Commons.

### Implementation

**New file:** `cmscontrib/DownloadMexicanStateFlags.py`

#### Responsibilities

- Hardcoded mapping of project state code → Wikimedia Commons filename for all 32 states (e.g. `"JAL": "Flag_of_Jalisco.svg"`).
- For each state, construct the Wikimedia thumbnail URL to retrieve a PNG render at 160 px width:
  ```
  https://upload.wikimedia.org/wikipedia/commons/thumb/<hash_dir>/<filename>/160px-<filename>.png
  ```
  The hash directory prefix required by Wikimedia is computed from the MD5 of the filename.
- Download using `requests`.
- Open with Pillow, resize/crop to exactly **160×100** using `Image.LANCZOS`.
- Save as `cmsranking/flags/<CODE>.png`, overwriting the placeholder.
- On any per-state error (HTTP error, corrupt image, unexpected size), log a warning and continue.

#### CLI interface

```
python cmscontrib/DownloadMexicanStateFlags.py [--output-dir PATH] [--states CODE ...]
```

- No arguments: downloads all 32 states to `cmsranking/flags/`.
- `--output-dir PATH`: write images to an alternate directory.
- `--states CODE ...`: download only the specified state codes.

#### Dependencies

- `requests` — already a project dependency.
- `Pillow>=10` — added as an optional dependency in `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  contrib = ["Pillow>=10"]
  ```

#### Resize strategy

Wikimedia delivers the thumbnail at the requested width. If the resulting image is not exactly 160×100, Pillow crops to center or pads with white to reach the exact target dimensions before saving.

### Workflow

1. Install contrib deps: `pip install -e ".[contrib]"`
2. Run the script: `python cmscontrib/DownloadMexicanStateFlags.py`
3. Review the downloaded images.
4. Commit `cmsranking/flags/*.png` to replace the placeholders.

The images then become bundled assets copied to `lib_dir/flags/` at ranking server startup via the existing `seed_flags_and_teams` mechanism.

---

## Files Changed

| File | Change |
|------|--------|
| `docs/RankingWebServer.rst` | Add custom logo documentation section |
| `pyproject.toml` | Add `[contrib]` optional dep with `Pillow>=10` |
| `cmscontrib/DownloadMexicanStateFlags.py` | New script |
| `cmsranking/flags/*.png` | Replaced after running the script |

## Out of Scope

- Modifying the logo mechanism in `RankingWebServer.py` (already works).
- Flags for non-Mexican contexts (generic flag support unchanged).
- Automated flag updates (script is run manually and results committed).
