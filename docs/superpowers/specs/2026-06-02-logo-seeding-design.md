# Ranking: Asset Seeding (Logo and Faces)

**Date:** 2026-06-02
**Status:** Approved

## Overview

Extend the ranking server startup to seed the logo and face images from the
package to `lib_dir/` automatically, following the same pattern already used
for flag images. Operators bundle their assets directly in `cmsranking/` and
the server deploys with everything in place — no volume mounts or manual copy
steps required.

---

## Motivation

Flags are seeded to `lib_dir/flags/` at startup via `seed_flags_and_teams`.
The logo and faces have no equivalent mechanism:

- The logo falls back to `cmsranking/static/img/logo.png` only when nothing
  exists in `lib_dir/` — no seeding occurs. The file lives in `static/img/`
  rather than alongside `flags/` and `faces/`, which is inconsistent.
- Faces have no bundled directory at all; `lib_dir/faces/` must be populated
  manually after each container start.

The goal is to make all three asset types work the same way: place files in
the package, start the server, everything is available.

---

## Design

### Asset locations

| Asset | Source in package | Destination in `lib_dir` |
|-------|-------------------|--------------------------|
| Flags | `cmsranking/flags/` (existing) | `lib_dir/flags/` |
| Logo | `cmsranking/logo.png` *(move from `static/img/`)* | `lib_dir/logo.png` |
| Faces | `cmsranking/faces/` *(new directory)* | `lib_dir/faces/` |

### Seeding behavior

**Logo** — always overwrites `lib_dir/logo.png` on startup. To use a
temporary custom logo, place a file at `lib_dir/logo.{ext}` while the server
is running; it is served immediately but overwritten on next restart. To make
a permanent change, replace `cmsranking/logo.png` in the package.

**Faces** — same pattern as flags: all images bundled in `cmsranking/faces/`
are copied to `lib_dir/faces/`, overwriting any existing file with the same
name. Files already in `lib_dir/faces/` that are not in the bundle are
preserved. No user registration is performed (unlike `seed_flags_and_teams`,
which registers teams — face images are linked to users imported separately).

### Code changes

**`cmsranking/faces/`** — new directory, committed to the repo. Operators
add participant face images here (e.g., `JAL001.png`).

**`cmsranking/seed.py`** — two new public functions:

```python
def seed_logo(lib_dir: str) -> None:
    """Copy the bundled logo to lib_dir, overwriting any existing file."""
    src = files("cmsranking") / "logo.png"
    dest = os.path.join(lib_dir, "logo.png")
    with open(dest, "wb") as f:
        f.write(src.read_bytes())


def seed_faces(lib_dir: str) -> None:
    """Copy all bundled face images to lib_dir/faces/, overwriting existing."""
    faces_dir = os.path.join(lib_dir, "faces")
    os.makedirs(faces_dir, exist_ok=True)
    bundled = files("cmsranking") / "faces"
    for resource in bundled.iterdir():
        if resource.is_file() and \
                os.path.splitext(resource.name)[1].lower() in _IMAGE_EXTS:
            dest = os.path.join(faces_dir, resource.name)
            with open(dest, "wb") as f:
                f.write(resource.read_bytes())
```

**`cmsranking/RankingWebServer.py`** — call alongside `seed_flags_and_teams`:

```python
from cmsranking.seed import seed_faces, seed_flags_and_teams, seed_logo
...
seed_flags_and_teams(config.lib_dir, stores["team"])
seed_logo(config.lib_dir)
seed_faces(config.lib_dir)
```

**`pyproject.toml`** — ensure `cmsranking/faces/` is included as package
data (verify `[tool.setuptools.package-data]` covers it).

**`cmsranking/RankingWebServer.py`** — also update the `ImageHandler` fallback
path for `/logo` from `static/img/logo.png` to `cmsranking/logo.png`:

```python
ImageHandler(
    os.path.join(config.lib_dir, '%(name)s'),
    os.path.join(web_dir, '..', 'logo.png')),  # fallback to package root
```

The `ImageHandler` routing for `/faces` is unchanged.

### Error handling

- **`seed_logo`**: raises `FileNotFoundError` if the bundled `logo.png` is
  missing (corrupted installation). Loud failure is intentional.
- **`seed_faces`**: if the bundled `faces/` directory is empty or missing,
  logs a warning and continues — an empty faces directory is valid (no
  bundled participant photos).

---

## Testing

File: `cmstestsuite/unit_tests/cmsranking/test_seed.py`

New test cases for `seed_logo`:
1. Copies the bundled file to `lib_dir/logo.png`.
2. Overwrites a pre-existing `logo.png` in `lib_dir`.

New test cases for `seed_faces`:
1. Copies all bundled face images to `lib_dir/faces/`.
2. Overwrites existing files with the same name.
3. Preserves files in `lib_dir/faces/` not present in the bundle.
4. Handles an empty `cmsranking/faces/` directory without error.

---

## Documentation changes

**`docs/ranking-mexico.md`** — update "Custom logo" section and add a
"Participant faces" section describing the seeding mechanism and the two
customization paths (permanent via package vs. temporary via `lib_dir`).

**`docs/RankingWebServer.rst`** — add equivalent sections for logo and faces.

---

## Files changed

| File | Change |
|------|--------|
| `cmsranking/logo.png` | Move from `cmsranking/static/img/logo.png` |
| `cmsranking/faces/` | New directory for bundled face images |
| `cmsranking/seed.py` | Add `seed_logo` and `seed_faces` functions |
| `cmsranking/RankingWebServer.py` | Import and call `seed_logo`, `seed_faces`; update fallback path |
| `pyproject.toml` | Verify `faces/` is included in package data |
| `cmstestsuite/unit_tests/cmsranking/test_seed.py` | Add test cases for both functions |
| `docs/ranking-mexico.md` | Update logo section, add faces section |
| `docs/RankingWebServer.rst` | Add logo and faces sections |

## Out of scope

- Automatic user registration from face filenames (faces are linked to users
  imported separately via `cmscontrib`).
- Supporting logo formats other than PNG in the bundled asset.
- A CLI script for asset management.
- Modifying Docker compose files (volume mounts are no longer needed for
  these assets).
