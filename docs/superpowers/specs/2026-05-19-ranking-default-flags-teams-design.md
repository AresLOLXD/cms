# Ranking Default Flags & Auto-Team Registration

**Date:** 2026-05-19
**Status:** Approved

## Overview

On startup, `cmsRankingWebServer` copies bundled default flag images (32 Mexican states) into `lib_dir/flags/` and auto-registers a team for every image file found in that directory. Users can override any flag by replacing the file on disk. User face images are served automatically if present, with no registration required.

---

## Architecture

### New files

- **`cmsranking/mx_states.py`** — Dict mapping the 32 Mexican state codes to their full names:
  ```python
  MX_STATES = {
      "AGU": "Aguascalientes",
      "BCA": "Baja California",
      "BCS": "Baja California Sur",
      "CAM": "Campeche",
      "CHH": "Chihuahua",
      "CHI": "Chiapas",
      "CMX": "Ciudad de México",
      "COA": "Coahuila",
      "COL": "Colima",
      "DUR": "Durango",
      "GUA": "Guanajuato",
      "GRO": "Guerrero",
      "HID": "Hidalgo",
      "JAL": "Jalisco",
      "MEX": "Estado de México",
      "MIC": "Michoacán",
      "MOR": "Morelos",
      "NAY": "Nayarit",
      "NLE": "Nuevo León",
      "OAX": "Oaxaca",
      "PUE": "Puebla",
      "QUE": "Querétaro",
      "ROO": "Quintana Roo",
      "SIN": "Sinaloa",
      "SLP": "San Luis Potosí",
      "SON": "Sonora",
      "TAB": "Tabasco",
      "TAM": "Tamaulipas",
      "TLA": "Tlaxcala",
      "VER": "Veracruz",
      "YUC": "Yucatán",
      "ZAC": "Zacatecas",
  }
  ```

- **`cmsranking/flags/`** — Package resource directory containing the 32 PNG flag images named `<CODE>.png` (e.g., `JAL.png`, `CMX.png`).

### Modified files

- **`cmsranking/RankingWebServer.py`** — `main()` calls `seed_flags_and_teams(config, stores["team"])` after `stores["team"].load_from_disk()`.

### New function: `seed_flags_and_teams`

Located in `cmsranking/RankingWebServer.py` (or extracted to `cmsranking/seed.py`):

```
seed_flags_and_teams(config: Config, team_store: Store[Team]) -> None
```

**Step 1 — Copy bundled flags (always overwrite):**
For each file in the `cmsranking/flags/` package resource directory, copy it to `lib_dir/flags/<filename>`, overwriting if it exists. This ensures the default state flags are always up to date when the package is updated.

**Step 2 — Auto-register teams (create only):**
Scan `lib_dir/flags/` for image files (`.png`, `.jpg`, `.gif`, `.bmp`). For each file:
- Derive `key` = filename without extension (e.g., `JAL.png` → `"JAL"`)
- Derive `name` = lookup `key` in `MX_STATES`; if not found, use `key` as name
- If `key` is not already in `team_store`: create the team `{"name": name}`
- If `key` already exists: skip (never overwrite existing team data)

---

## Behavior Rules

| Situation | Image | Team |
|-----------|-------|------|
| Bundled flag (`JAL.png`) on startup | Always overwritten from package | Created if missing; skipped if exists |
| Custom flag (`USA.png`) added by user | Never touched | Created if missing; skipped if exists |
| User replaces any flag file on disk | New file is served immediately (no restart needed) | Unchanged |
| Admin edits team name via admin UI | Unchanged | Updated in store |
| Flag file is deleted | No change (manual cleanup required) | Unchanged |

Images are served directly from disk on every request — there is no image caching in the ranking server. Replacing a file takes effect immediately.

---

## User Documentation

### Changing a state flag

Replace the image file in `lib_dir/flags/<CODE>.<ext>` (default: `~/.venv/lib/ranking/flags/`). The new image is served immediately without restarting the server.

Supported formats: `.png`, `.jpg`, `.gif`, `.bmp`.

### Adding a custom team with a flag

1. Place an image file in `lib_dir/flags/<YOUR_CODE>.png`.
2. Restart `cmsRankingWebServer`.
3. The server creates a team with key `YOUR_CODE` and name `YOUR_CODE` automatically.
4. To set a friendlier display name, edit the team from the admin interface.

### Bundled default flags

The 32 Mexican state flags are bundled with the package and copied to `lib_dir/flags/` every time the server starts. If you want to permanently replace one of the default state flags, place your image at `lib_dir/flags/<CODE>.png` **after** the server has started (or stop the server, replace the file, then start again — the startup copy runs before requests are served, so replace it after).

> **Note:** The startup copy always overwrites bundled flag files. To permanently use a custom image for a Mexican state code, you would need to modify the bundled resources in the package itself.

### User face images

Place an image at `lib_dir/faces/<username>.<ext>`. It is served automatically at `/faces/<username>` when a contestant has that username. If no image is found, a generic placeholder is shown. No registration or server restart is needed.

Supported formats: `.png`, `.jpg`, `.gif`, `.bmp`.

---

## Out of Scope

- Automatic team deletion when a flag file is removed.
- Faces auto-registration or default face bundles.
- Non-Mexican default team sets.
