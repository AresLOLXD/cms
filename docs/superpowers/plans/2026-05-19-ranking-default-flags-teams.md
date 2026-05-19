# Ranking Default Flags & Auto-Team Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On startup, `cmsRankingWebServer` copies bundled Mexican state flag images to `lib_dir/flags/` and auto-registers a team for every image file found in that directory.

**Architecture:** A new `cmsranking/seed.py` module exposes `seed_flags_and_teams(lib_dir, team_store)` which first copies bundled flags (always overwrite) then scans the flags directory and creates teams for any image whose key is not yet registered. A `cmsranking/mx_states.py` dict drives the code→name mapping. Both are called from `RankingWebServer.main()` after `load_from_disk()`.

**Tech Stack:** Python 3.11+, importlib.resources (stdlib), unittest, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `cmsranking/mx_states.py` | Create | Dict of 32 Mexican state codes → full names |
| `cmsranking/flags/` | Create | 32 placeholder PNG images bundled as package resources |
| `cmsranking/seed.py` | Create | `seed_flags_and_teams()` — copy bundled flags + register teams |
| `cmsranking/RankingWebServer.py` | Modify | Call `seed_flags_and_teams()` in `main()` |
| `setup.py` | Modify | Add `"flags/*.*"` to `cmsranking` PACKAGE_DATA |
| `cmstestsuite/unit_tests/cmsranking/__init__.py` | Create | Makes directory a Python package for pytest |
| `cmstestsuite/unit_tests/cmsranking/test_mx_states.py` | Create | Tests for mx_states dict |
| `cmstestsuite/unit_tests/cmsranking/test_seed.py` | Create | Tests for seed_flags_and_teams |
| `docs/RankingWebServer.rst` | Modify | User documentation for flags and faces |

---

### Task 1: `cmsranking/mx_states.py` — state code dictionary

**Files:**
- Create: `cmsranking/mx_states.py`
- Create: `cmstestsuite/unit_tests/cmsranking/__init__.py`
- Create: `cmstestsuite/unit_tests/cmsranking/test_mx_states.py`

- [ ] **Step 1: Create the test package init file**

```bash
touch cmstestsuite/unit_tests/cmsranking/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `cmstestsuite/unit_tests/cmsranking/test_mx_states.py`:

```python
#!/usr/bin/env python3

import unittest

from cmsranking.mx_states import MX_STATES


class TestMxStates(unittest.TestCase):

    def test_has_32_entries(self):
        self.assertEqual(len(MX_STATES), 32)

    def test_keys_are_three_uppercase_letters(self):
        for code in MX_STATES:
            self.assertEqual(len(code), 3)
            self.assertTrue(code.isupper())
            self.assertTrue(code.isalpha())

    def test_values_are_nonempty_strings(self):
        for name in MX_STATES.values():
            self.assertIsInstance(name, str)
            self.assertGreater(len(name), 0)

    def test_known_entries(self):
        self.assertEqual(MX_STATES["JAL"], "Jalisco")
        self.assertEqual(MX_STATES["CMX"], "Ciudad de México")
        self.assertEqual(MX_STATES["MEX"], "Estado de México")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests — expect FAIL (module not found)**

```bash
pytest cmstestsuite/unit_tests/cmsranking/test_mx_states.py -v
```

Expected: `ModuleNotFoundError: No module named 'cmsranking.mx_states'`

- [ ] **Step 4: Create `cmsranking/mx_states.py`**

```python
#!/usr/bin/env python3

MX_STATES: dict[str, str] = {
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

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest cmstestsuite/unit_tests/cmsranking/test_mx_states.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add cmsranking/mx_states.py cmstestsuite/unit_tests/cmsranking/__init__.py cmstestsuite/unit_tests/cmsranking/test_mx_states.py
git commit -m "feat: add Mexican state code dictionary for ranking"
```

---

### Task 2: Bundled flag images + setup.py

**Files:**
- Create: `cmsranking/flags/` (32 PNG files)
- Modify: `setup.py`

> **Note:** These are placeholder 40×25 pixel PNG images. Replace with real state flag images before deploying to production. The filenames must match the codes in `MX_STATES` exactly (e.g. `JAL.png`).

- [ ] **Step 1: Create the flags directory and placeholder PNGs**

Run this Python script once from the repo root to generate the placeholder files:

```bash
python3 - <<'EOF'
import os, struct, zlib

STATES = [
    "AGU","BCA","BCS","CAM","CHH","CHI","CMX","COA",
    "COL","DUR","GUA","GRO","HID","JAL","MEX","MIC",
    "MOR","NAY","NLE","OAX","PUE","QUE","ROO","SIN",
    "SLP","SON","TAB","TAM","TLA","VER","YUC","ZAC",
]

COLORS = [
    (0,70,153),(180,0,0),(0,140,0),(200,150,0),
    (100,0,150),(0,100,100),(150,80,0),(0,0,100),
]

def chunk(tag, data):
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

def make_png(r, g, b, w=40, h=25):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    row = bytes([0] + [r, g, b] * w)
    idat = chunk(b"IDAT", zlib.compress(row * h))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend

out = os.path.join("cmsranking", "flags")
os.makedirs(out, exist_ok=True)
for i, code in enumerate(STATES):
    r, g, b = COLORS[i % len(COLORS)]
    path = os.path.join(out, f"{code}.png")
    with open(path, "wb") as f:
        f.write(make_png(r, g, b))
    print(f"Created {path}")
print(f"\nDone — {len(STATES)} placeholder PNGs created.")
print("Replace with real flag images before production deployment.")
EOF
```

- [ ] **Step 2: Verify all 32 files were created**

```bash
ls cmsranking/flags/ | wc -l
```

Expected output: `32`

```bash
ls cmsranking/flags/
```

Expected: `AGU.png  BCA.png  BCS.png  CAM.png  CHH.png  CHI.png  CMX.png  COA.png  COL.png  DUR.png  GUA.png  GRO.png  HID.png  JAL.png  MEX.png  MIC.png  MOR.png  NAY.png  NLE.png  OAX.png  PUE.png  QUE.png  ROO.png  SIN.png  SLP.png  SON.png  TAB.png  TAM.png  TLA.png  VER.png  YUC.png  ZAC.png`

- [ ] **Step 3: Update `setup.py` to include the flags as package data**

In `setup.py`, find the `PACKAGE_DATA` dict. The `"cmsranking"` entry currently ends with:

```python
    "cmsranking": [
        "static/img/*.*",
        "static/lib/*.*",
        "static/*.*",
    ],
```

Add the flags pattern:

```python
    "cmsranking": [
        "static/img/*.*",
        "static/lib/*.*",
        "static/*.*",
        "flags/*.*",
    ],
```

- [ ] **Step 4: Reinstall the package so the new resources are discoverable**

```bash
pip install -e ".[devel]"
```

- [ ] **Step 5: Verify the bundled flags are accessible via importlib.resources**

```bash
python3 -c "
from importlib.resources import files
flags = files('cmsranking') / 'flags'
names = sorted(r.name for r in flags.iterdir() if r.is_file())
print(f'{len(names)} bundled flags:', names[:4], '...')
"
```

Expected: `32 bundled flags: ['AGU.png', 'BCA.png', 'BCS.png', 'CAM.png'] ...`

- [ ] **Step 6: Commit**

```bash
git add cmsranking/flags/ setup.py
git commit -m "feat: add bundled placeholder flag images for 32 Mexican states"
```

---

### Task 3: `cmsranking/seed.py` — seed function + tests

**Files:**
- Create: `cmsranking/seed.py`
- Create: `cmstestsuite/unit_tests/cmsranking/test_seed.py`

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/cmsranking/test_seed.py`:

```python
#!/usr/bin/env python3

import os
import struct
import unittest
import zlib
from unittest.mock import patch

import pytest

from cmsranking.seed import _copy_bundled_flags, _register_teams_from_flags
from cmsranking.Store import Store
from cmsranking.Team import Team


def _make_png(r=100, g=100, b=200, w=4, h=4):
    """Create a minimal valid PNG for testing."""
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    row = bytes([0] + [r, g, b] * w)
    idat = chunk(b"IDAT", zlib.compress(row * h))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _make_team_store(tmp_path):
    stores = {}
    store = Store(Team, str(tmp_path / "teams"), stores)
    store.load_from_disk()
    return store


class TestRegisterTeamsFromFlags(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.flags_dir = os.path.join(self._tmp, "flags")
        os.makedirs(self.flags_dir)
        self._teams_dir = os.path.join(self._tmp, "teams")
        os.makedirs(self._teams_dir)
        stores = {}
        self.team_store = Store(Team, self._teams_dir, stores)
        self.team_store.load_from_disk()

    def _add_image(self, filename):
        path = os.path.join(self.flags_dir, filename)
        with open(path, "wb") as f:
            f.write(_make_png())

    def test_creates_team_for_known_mx_state(self):
        self._add_image("JAL.png")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("JAL", self.team_store)
        self.assertEqual(self.team_store.retrieve("JAL")["name"], "Jalisco")

    def test_creates_team_for_unknown_code_using_filename(self):
        self._add_image("USA.png")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("USA", self.team_store)
        self.assertEqual(self.team_store.retrieve("USA")["name"], "USA")

    def test_skips_existing_teams(self):
        self._add_image("JAL.png")
        self.team_store.create("JAL", {"name": "Custom Name"})
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertEqual(self.team_store.retrieve("JAL")["name"], "Custom Name")

    def test_ignores_non_image_files(self):
        path = os.path.join(self.flags_dir, "README.txt")
        with open(path, "w") as f:
            f.write("not an image")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertNotIn("README", self.team_store)

    def test_registers_multiple_images(self):
        for code in ("AGU", "BCA", "CUSTOM"):
            self._add_image(f"{code}.png")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("AGU", self.team_store)
        self.assertIn("BCA", self.team_store)
        self.assertIn("CUSTOM", self.team_store)

    def test_supported_extensions(self):
        for ext in (".png", ".jpg", ".gif", ".bmp"):
            self._add_image(f"TST{ext}")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("TST", self.team_store)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp)


class TestCopyBundledFlags(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.flags_dir = os.path.join(self._tmp, "flags")
        os.makedirs(self.flags_dir)

    def test_copies_bundled_flags_to_dir(self):
        _copy_bundled_flags(self.flags_dir)
        files = os.listdir(self.flags_dir)
        self.assertEqual(len(files), 32)
        self.assertIn("JAL.png", files)

    def test_overwrites_existing_file(self):
        target = os.path.join(self.flags_dir, "JAL.png")
        with open(target, "wb") as f:
            f.write(b"old content")
        _copy_bundled_flags(self.flags_dir)
        with open(target, "rb") as f:
            content = f.read()
        self.assertNotEqual(content, b"old content")
        self.assertTrue(content.startswith(b"\x89PNG"))

    def test_does_not_copy_non_image_files(self):
        _copy_bundled_flags(self.flags_dir)
        for name in os.listdir(self.flags_dir):
            ext = os.path.splitext(name)[1].lower()
            self.assertIn(ext, {".png", ".jpg", ".jpeg", ".gif", ".bmp"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

```bash
pytest cmstestsuite/unit_tests/cmsranking/test_seed.py -v
```

Expected: `ModuleNotFoundError: No module named 'cmsranking.seed'`

- [ ] **Step 3: Create `cmsranking/seed.py`**

```python
#!/usr/bin/env python3

import logging
import os
from importlib.resources import files

from cmsranking.mx_states import MX_STATES
from cmsranking.Store import Store
from cmsranking.Team import Team

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}


def _copy_bundled_flags(flags_dir: str) -> None:
    """Copy all bundled flag images to flags_dir, overwriting existing files."""
    bundled = files("cmsranking") / "flags"
    for resource in bundled.iterdir():
        if not resource.is_file():
            continue
        ext = os.path.splitext(resource.name)[1].lower()
        if ext not in _IMAGE_EXTS:
            continue
        dest = os.path.join(flags_dir, resource.name)
        with open(dest, "wb") as f:
            f.write(resource.read_bytes())


def _register_teams_from_flags(flags_dir: str, team_store: Store) -> None:
    """Create a team for each image in flags_dir that has no team entry yet."""
    for filename in os.listdir(flags_dir):
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTS:
            continue
        if stem in team_store:
            continue
        name = MX_STATES.get(stem, stem)
        try:
            team_store.create(stem, {"name": name})
            logger.info("Registered team '%s' (%s) from flag image.", stem, name)
        except Exception:
            logger.warning("Could not register team '%s'.", stem, exc_info=True)


def seed_flags_and_teams(lib_dir: str, team_store: Store) -> None:
    """Copy bundled flags and auto-register teams on ranking server startup."""
    flags_dir = os.path.join(lib_dir, "flags")
    os.makedirs(flags_dir, exist_ok=True)
    _copy_bundled_flags(flags_dir)
    _register_teams_from_flags(flags_dir, team_store)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest cmstestsuite/unit_tests/cmsranking/test_seed.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest cmstestsuite/unit_tests/ -v
```

Expected: all previously passing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add cmsranking/seed.py cmstestsuite/unit_tests/cmsranking/test_seed.py
git commit -m "feat: add seed_flags_and_teams for auto-registering teams from flag images"
```

---

### Task 4: Wire `seed_flags_and_teams` into `RankingWebServer.main()`

**Files:**
- Modify: `cmsranking/RankingWebServer.py`

- [ ] **Step 1: Add the import at the top of `RankingWebServer.py`**

Find the block of `cmsranking` imports (around line 50):

```python
from cmsranking.Config import PublicConfig, load_config
from cmsranking.Contest import Contest
from cmsranking.Entity import InvalidData
from cmsranking.Scoring import ScoringStore
from cmsranking.Store import Store
from cmsranking.Subchange import Subchange
from cmsranking.Submission import Submission
from cmsranking.Task import Task
from cmsranking.Team import Team
from cmsranking.User import User
```

Add the seed import after the last `cmsranking` import:

```python
from cmsranking.Config import PublicConfig, load_config
from cmsranking.Contest import Contest
from cmsranking.Entity import InvalidData
from cmsranking.Scoring import ScoringStore
from cmsranking.seed import seed_flags_and_teams
from cmsranking.Store import Store
from cmsranking.Subchange import Subchange
from cmsranking.Submission import Submission
from cmsranking.Task import Task
from cmsranking.Team import Team
from cmsranking.User import User
```

- [ ] **Step 2: Call `seed_flags_and_teams` in `main()` after `load_from_disk()`**

Find the `load_from_disk()` calls in `main()` (around line 602–607):

```python
    stores["contest"].load_from_disk()
    stores["task"].load_from_disk()
    stores["team"].load_from_disk()
    stores["user"].load_from_disk()
    stores["submission"].load_from_disk()
    stores["subchange"].load_from_disk()
```

Add the seed call immediately after:

```python
    stores["contest"].load_from_disk()
    stores["task"].load_from_disk()
    stores["team"].load_from_disk()
    stores["user"].load_from_disk()
    stores["submission"].load_from_disk()
    stores["subchange"].load_from_disk()

    seed_flags_and_teams(config.lib_dir, stores["team"])
```

- [ ] **Step 3: Verify the server starts without errors**

```bash
python3 -c "
import sys
sys.argv = ['cmsRankingWebServer']
# Just import and check main() is importable without crashing
from cmsranking.RankingWebServer import main
print('Import OK')
"
```

Expected: `Import OK`

- [ ] **Step 4: Run the full test suite**

```bash
pytest cmstestsuite/unit_tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cmsranking/RankingWebServer.py
git commit -m "feat: call seed_flags_and_teams on ranking server startup"
```

---

### Task 5: User documentation

**Files:**
- Modify: `docs/RankingWebServer.rst`

- [ ] **Step 1: Read the existing docs file to find the right insertion point**

```bash
grep -n "flag\|team\|image\|face" docs/RankingWebServer.rst -i | head -20
```

- [ ] **Step 2: Add a new section for flags, teams, and faces**

Append the following section at the end of `docs/RankingWebServer.rst` (before any trailing content, or at the very end):

```rst
Managing flags, teams, and user faces
--------------------------------------

Flags and auto-registered teams
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When :program:`cmsRankingWebServer` starts, it:

1. Copies the 32 bundled Mexican state flag images into ``lib_dir/flags/``,
   overwriting any previous copies of those files.
2. Scans ``lib_dir/flags/`` for image files (``.png``, ``.jpg``, ``.gif``,
   ``.bmp``) and creates a **team** entry for each one that is not yet
   registered.

The team name is derived from the filename: if the filename matches a known
Mexican state code (e.g. ``JAL``), the full state name is used
(``Jalisco``). Otherwise the filename itself becomes the team name.

Teams are only *created*, never automatically updated or deleted. Once a
team exists, you can rename it or modify it through the admin interface
without losing your changes on the next restart.

**Replacing a default state flag**

Replace the file at ``lib_dir/flags/<CODE>.png`` while the server is
running. The new image is served immediately — no restart required. The
file is overwritten by the bundled default on the next server startup, so
to make the change permanent, also replace the source file at::

    cmsranking/flags/<CODE>.png

inside the package installation directory (e.g.
``.venv/lib/python3.x/site-packages/cmsranking/flags/``).

**Adding a custom team with a flag**

1. Place an image file at ``lib_dir/flags/<YOUR_CODE>.png``.
2. Restart :program:`cmsRankingWebServer`.
3. A team with key ``YOUR_CODE`` is created automatically. To set a
   friendlier display name, edit the team from the admin interface.

**Changing any flag image**

Simply replace the image file at ``lib_dir/flags/<CODE>.<ext>`` with a
new file of the same name. Images are read from disk on every HTTP
request, so the change takes effect immediately without a restart.

Supported formats: ``.png``, ``.jpg``, ``.gif``, ``.bmp``.

User face images
~~~~~~~~~~~~~~~~

To display a photo for a contestant, place an image at::

    lib_dir/faces/<username>.<ext>

where ``<username>`` is the contestant's login username. The image is
served at ``/faces/<username>`` automatically. If no image is found, a
generic placeholder is shown.

No registration or server restart is required — just add the file and it
appears immediately.

Supported formats: ``.png``, ``.jpg``, ``.gif``, ``.bmp``.

Default lib_dir location
~~~~~~~~~~~~~~~~~~~~~~~~~

Unless overridden in ``cms_ranking.toml``, ``lib_dir`` defaults to::

    <venv-prefix>/lib/ranking/

For example, if you installed CMS inside ``.venv``::

    .venv/lib/ranking/flags/   ← flag images
    .venv/lib/ranking/faces/   ← user face images
```

- [ ] **Step 3: Verify the RST syntax is valid**

```bash
python3 -c "
import docutils.parsers.rst
import docutils.frontend
import docutils.utils
settings = docutils.frontend.OptionParser(
    components=(docutils.parsers.rst.Parser,)).get_default_values()
document = docutils.utils.new_document('test', settings)
parser = docutils.parsers.rst.Parser()
with open('docs/RankingWebServer.rst') as f:
    parser.parse(f.read(), document)
print('RST OK')
"
```

Expected: `RST OK` (or any output without a Python exception).

- [ ] **Step 4: Commit**

```bash
git add docs/RankingWebServer.rst
git commit -m "docs: document flag management, auto-team registration, and user faces"
```

---

## Self-Review

**Spec coverage:**
- ✅ Bundled default flags (32 states) — Task 2
- ✅ Flags always overwrite on startup — Task 3, `_copy_bundled_flags`
- ✅ Auto-register teams from any image — Task 3, `_register_teams_from_flags`
- ✅ MX state codes get full names; others use filename — Task 1 + Task 3
- ✅ Existing teams are never overwritten — Task 3 test `test_skips_existing_teams`
- ✅ Images served from disk, changes instant — behavior preserved from existing `ImageHandler`
- ✅ User documentation (flags, custom teams, faces) — Task 5

**Placeholder scan:** No TBDs or incomplete steps found.

**Type consistency:** `seed_flags_and_teams(lib_dir: str, team_store: Store)` used consistently across Task 3 (definition) and Task 4 (call site).
