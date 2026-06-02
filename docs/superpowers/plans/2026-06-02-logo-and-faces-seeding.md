# Logo and Faces Asset Seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed the bundled logo and face images from the package to `lib_dir/` on ranking server startup, following the same pattern used for flag images.

**Architecture:** Add two public functions — `seed_logo` and `seed_faces` — to `cmsranking/seed.py`, then call them from `RankingWebServer.py` alongside the existing `seed_flags_and_teams`. The `ImageHandler` routing is unchanged; this only affects what gets written to disk at startup.

**Tech Stack:** Python 3.11, `importlib.resources.files`, `unittest` with `unittest.mock`.

---

## File Map

| File | Action |
|------|--------|
| `cmsranking/seed.py` | Add `seed_logo` and `seed_faces` functions |
| `cmsranking/faces/` | Create directory; add `.gitkeep` |
| `cmsranking/RankingWebServer.py` | Import and call `seed_logo`, `seed_faces` |
| `setup.py` | Add `"faces/*.*"` to `cmsranking` package data |
| `cmstestsuite/unit_tests/cmsranking/test_seed.py` | Add `TestSeedLogo` and `TestSeedFaces` test classes |
| `docs/ranking-mexico.md` | Update logo section; add faces section |
| `docs/RankingWebServer.rst` | Add logo and faces sections |

---

## Task 1: `seed_logo` — tests and implementation

**Files:**
- Modify: `cmstestsuite/unit_tests/cmsranking/test_seed.py`
- Modify: `cmsranking/seed.py`

- [ ] **Step 1: Write the failing tests**

Open `cmstestsuite/unit_tests/cmsranking/test_seed.py`. Add this import at the top alongside the existing ones:

```python
from cmsranking.seed import _copy_bundled_flags, _register_teams_from_flags, seed_logo
```

Add this class after the existing test classes (before `if __name__ == "__main__":`):

```python
class TestSeedLogo(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def test_copies_bundled_logo_to_lib_dir(self):
        seed_logo(self._tmp)
        dest = os.path.join(self._tmp, "logo.png")
        self.assertTrue(os.path.isfile(dest))
        with open(dest, "rb") as f:
            content = f.read()
        self.assertTrue(content.startswith(b"\x89PNG"))

    def test_overwrites_existing_logo(self):
        dest = os.path.join(self._tmp, "logo.png")
        with open(dest, "wb") as f:
            f.write(b"old content")
        seed_logo(self._tmp)
        with open(dest, "rb") as f:
            content = f.read()
        self.assertNotEqual(content, b"old content")
        self.assertTrue(content.startswith(b"\x89PNG"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_seed.py::TestSeedLogo -v
```

Expected: `ImportError: cannot import name 'seed_logo'`

- [ ] **Step 3: Implement `seed_logo` in `seed.py`**

Open `cmsranking/seed.py`. Add this function at the end of the file:

```python
def seed_logo(lib_dir: str) -> None:
    """Copy the bundled logo to lib_dir, overwriting any existing file."""
    src = files("cmsranking") / "static" / "img" / "logo.png"
    dest = os.path.join(lib_dir, "logo.png")
    with open(dest, "wb") as f:
        f.write(src.read_bytes())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_seed.py::TestSeedLogo -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add cmsranking/seed.py cmstestsuite/unit_tests/cmsranking/test_seed.py
git commit -m "feat: add seed_logo to copy bundled logo on ranking server startup"
```

---

## Task 2: Create `cmsranking/faces/` and register package data

**Files:**
- Create: `cmsranking/faces/.gitkeep`
- Modify: `setup.py`

- [ ] **Step 1: Create the directory with a placeholder**

```bash
mkdir cmsranking/faces
touch cmsranking/faces/.gitkeep
```

- [ ] **Step 2: Register `faces/*.*` as package data**

Open `setup.py`. Find the `"cmsranking"` entry in `PACKAGE_DATA` (around line 59):

```python
    "cmsranking": [
        "static/img/*.*",
        "static/lib/*.*",
        "static/*.*",
        "flags/*.*",
    ],
```

Add `"faces/*.*"` to the list:

```python
    "cmsranking": [
        "static/img/*.*",
        "static/lib/*.*",
        "static/*.*",
        "flags/*.*",
        "faces/*.*",
    ],
```

- [ ] **Step 3: Verify the package sees the new directory**

```bash
.venv/bin/python3 -c "from importlib.resources import files; d = files('cmsranking') / 'faces'; print(list(d.iterdir()))"
```

Expected: `[]` (empty list, no error)

- [ ] **Step 4: Commit**

```bash
git add cmsranking/faces/.gitkeep setup.py
git commit -m "feat: add cmsranking/faces/ bundle directory and register as package data"
```

---

## Task 3: `seed_faces` — tests and implementation

**Files:**
- Modify: `cmstestsuite/unit_tests/cmsranking/test_seed.py`
- Modify: `cmsranking/seed.py`

The `cmsranking/faces/` directory starts empty (operators add their own images). Tests therefore mock the bundled resource to control what `files("cmsranking") / "faces"` returns.

- [ ] **Step 1: Add the import for `seed_faces` and `mock`**

In `cmstestsuite/unit_tests/cmsranking/test_seed.py`, update the import line from Task 1:

```python
from cmsranking.seed import _copy_bundled_flags, _register_teams_from_flags, seed_faces, seed_logo
```

Also add at the top with the other stdlib imports:

```python
from unittest.mock import MagicMock, patch
```

- [ ] **Step 2: Write the failing tests**

Add this class after `TestSeedLogo` (before `if __name__ == "__main__":`):

```python
class TestSeedFaces(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def _fake_resource(self, filename: str, content: bytes) -> MagicMock:
        r = MagicMock()
        r.name = filename
        r.is_file.return_value = True
        r.read_bytes.return_value = content
        return r

    def _patch_faces(self, resources: list) -> "AbstractContextManager":
        mock_bundled = MagicMock()
        mock_bundled.iterdir.return_value = resources

        def truediv(_, key):
            return mock_bundled

        mock_pkg = MagicMock()
        mock_pkg.__truediv__ = truediv
        return patch("cmsranking.seed.files", return_value=mock_pkg)

    def test_copies_bundled_faces_to_lib_dir(self):
        content = _make_png(r=255, g=0, b=0)
        resources = [self._fake_resource("USER001.png", content)]
        with self._patch_faces(resources):
            seed_faces(self._tmp)
        dest = os.path.join(self._tmp, "faces", "USER001.png")
        self.assertTrue(os.path.isfile(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), content)

    def test_overwrites_existing_face(self):
        faces_dir = os.path.join(self._tmp, "faces")
        os.makedirs(faces_dir)
        dest = os.path.join(faces_dir, "USER001.png")
        with open(dest, "wb") as f:
            f.write(b"old content")
        new_content = _make_png(r=0, g=255, b=0)
        resources = [self._fake_resource("USER001.png", new_content)]
        with self._patch_faces(resources):
            seed_faces(self._tmp)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), new_content)

    def test_preserves_custom_faces_not_in_bundle(self):
        faces_dir = os.path.join(self._tmp, "faces")
        os.makedirs(faces_dir)
        custom = os.path.join(faces_dir, "CUSTOM.png")
        with open(custom, "wb") as f:
            f.write(_make_png())
        resources = [self._fake_resource("USER001.png", _make_png())]
        with self._patch_faces(resources):
            seed_faces(self._tmp)
        self.assertTrue(os.path.isfile(custom))

    def test_empty_bundle_does_not_raise(self):
        with self._patch_faces([]):
            try:
                seed_faces(self._tmp)
            except Exception as exc:
                self.fail(f"seed_faces raised {exc!r} on empty bundle")
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_seed.py::TestSeedFaces -v
```

Expected: `ImportError: cannot import name 'seed_faces'`

- [ ] **Step 4: Implement `seed_faces` in `seed.py`**

Open `cmsranking/seed.py`. Add this function after `seed_logo`:

```python
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

- [ ] **Step 5: Run all seed tests to verify they pass**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_seed.py -v
```

Expected: all tests pass (existing tests + 6 new ones)

- [ ] **Step 6: Commit**

```bash
git add cmsranking/seed.py cmstestsuite/unit_tests/cmsranking/test_seed.py
git commit -m "feat: add seed_faces to copy bundled face images on ranking server startup"
```

---

## Task 4: Wire up calls in `RankingWebServer.py`

**Files:**
- Modify: `cmsranking/RankingWebServer.py:51`

- [ ] **Step 1: Update the import**

Open `cmsranking/RankingWebServer.py`. Find line 51:

```python
from cmsranking.seed import seed_flags_and_teams
```

Replace with:

```python
from cmsranking.seed import seed_faces, seed_flags_and_teams, seed_logo
```

- [ ] **Step 2: Add the calls after `seed_flags_and_teams`**

Find line 610:

```python
    seed_flags_and_teams(config.lib_dir, stores["team"])
```

Replace with:

```python
    seed_flags_and_teams(config.lib_dir, stores["team"])
    seed_logo(config.lib_dir)
    seed_faces(config.lib_dir)
```

- [ ] **Step 3: Verify import is clean (no pyflakes warnings)**

```bash
.venv/bin/python3 -m pyflakes cmsranking/RankingWebServer.py
```

Expected: no output

- [ ] **Step 4: Commit**

```bash
git add cmsranking/RankingWebServer.py
git commit -m "feat: call seed_logo and seed_faces on ranking server startup"
```

---

## Task 5: Documentation

**Files:**
- Modify: `docs/ranking-mexico.md`
- Modify: `docs/RankingWebServer.rst` (create section if missing)

- [ ] **Step 1: Update `docs/ranking-mexico.md`**

Find the "Custom logo" section (around line 85) and replace it with:

```markdown
## Custom logo

To permanently change the ranking server logo, replace
`cmsranking/static/img/logo.png` in the package. On every server startup,
this file is copied automatically to `lib_dir/logo.png` and served at `/logo`.

To use a temporary logo without modifying the package, place a file directly
at `lib_dir/logo.{png,jpg,gif,bmp}` while the server is running — it is
served immediately. It will be overwritten on the next restart.

## Participant faces

Place participant face images in `cmsranking/faces/` (e.g., `JAL001.png`).
On every server startup, all images in that directory are copied to
`lib_dir/faces/` and served at `/faces/<filename>`. Files already in
`lib_dir/faces/` that are not in the bundle are preserved.

To use a temporary face without modifying the package, place a file directly
at `lib_dir/faces/<code>.{png,jpg,gif,bmp}` while the server is running — it
is served immediately. It will be overwritten on the next restart if a
bundled image with the same name exists.
```

- [ ] **Step 2: Add sections to `docs/RankingWebServer.rst`**

Open `docs/RankingWebServer.rst`. Add the following section at the end of the file (or after any existing asset-related section):

```rst
Custom logo
-----------

To permanently change the ranking server logo, replace
``cmsranking/static/img/logo.png`` in the package. On every server startup,
this file is copied automatically to ``lib_dir/logo.png`` and served at
``/logo``.

To use a temporary logo without modifying the package, place a file directly
at ``lib_dir/logo.{png,jpg,gif,bmp}`` while the server is running — it is
served immediately but will be overwritten on the next restart.

Participant faces
-----------------

Place participant face images in ``cmsranking/faces/`` (e.g., ``JAL001.png``).
On every server startup, all images in that directory are copied to
``lib_dir/faces/`` and served at ``/faces/<filename>``. Files already in
``lib_dir/faces/`` that are not in the bundle are preserved.

To use a temporary face without modifying the package, place a file directly
at ``lib_dir/faces/<code>.{png,jpg,gif,bmp}`` while the server is running —
it is served immediately but will be overwritten on the next restart if a
bundled image with the same name exists.
```

- [ ] **Step 3: Commit**

```bash
git add docs/ranking-mexico.md docs/RankingWebServer.rst
git commit -m "docs: document logo and faces seeding for ranking server operators"
```

---

## Self-Review

**Spec coverage:**
- `seed_logo` ✓ Task 1
- `seed_faces` ✓ Task 3
- `cmsranking/faces/` directory ✓ Task 2
- `setup.py` package data ✓ Task 2
- `RankingWebServer.py` wiring ✓ Task 4
- `docs/ranking-mexico.md` ✓ Task 5
- `docs/RankingWebServer.rst` ✓ Task 5
- Logo always overwrites ✓ Task 1 (test + impl)
- Faces overwrite bundled, preserve custom ✓ Task 3 (tests + impl)
- Empty faces dir no error ✓ Task 3 (test + impl)

**Placeholder scan:** No TBDs, no "similar to above", all code blocks complete.

**Type consistency:** `seed_logo(lib_dir: str) -> None` and `seed_faces(lib_dir: str) -> None` — consistent across all tasks. Import in Task 4 matches function names defined in Tasks 1 and 3.
