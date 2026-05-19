# Real Mexican State Flags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 32 placeholder solid-color flag images with real Mexican state flag images downloaded from Wikimedia Commons and committed as bundled assets.

**Architecture:** A new contrib script (`cmscontrib/DownloadMexicanStateFlags.py`) maps state codes to Wikimedia Commons SVG filenames, fetches 160 px-wide PNG thumbnails via the Wikimedia `Special:FilePath` redirect, resizes/pads each to exactly 160×100 px using Pillow, and writes the result to `cmsranking/flags/<CODE>.png`. The generated images are then committed to replace the placeholders.

**Tech Stack:** Python 3.11+, `requests` (already in deps), `Pillow>=10` (new optional dep), Wikimedia Commons thumbnail API.

---

## File Map

| Action | Path |
|--------|------|
| Modify | `pyproject.toml` |
| Create | `cmscontrib/DownloadMexicanStateFlags.py` |
| Create | `cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py` |
| Replace | `cmsranking/flags/*.png` (32 files, after running the script) |

---

## Task 1: Add Pillow as optional contrib dependency

**Files:**
- Modify: `pyproject.toml:53-65`

- [ ] **Step 1: Add the `contrib` optional-dependencies group**

In `pyproject.toml`, after the `devel` group (before `[build-system]`), add:

```toml
contrib = [
    # Only for cmscontrib scripts that process images
    "Pillow>=10",
]
```

The full `[project.optional-dependencies]` block becomes:

```toml
[project.optional-dependencies]
devel = [
    # Only for testing
    "beautifulsoup4>=4.8,<4.14",
    "coverage>=4.5,<7.10",
    "pytest",
    "pytest-cov",

    # Only for building documentation
    # XXX: The version of Sphinx needed to build our documentation
    # is incompatible with the old version of babel we need.
    # "Sphinx>=1.8,<1.9",
]

contrib = [
    # Only for cmscontrib scripts that process images
    "Pillow>=10",
]
```

- [ ] **Step 2: Install the new optional dependency**

```bash
.venv/bin/pip install -e ".[contrib]"
```

Expected output: `Successfully installed Pillow-<version>` (or `Requirement already satisfied`).

- [ ] **Step 3: Verify Pillow is available**

```bash
.venv/bin/python3 -c "from PIL import Image; print(Image.__version__)"
```

Expected: a version number like `10.x.x`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add Pillow as optional contrib dependency for image processing scripts"
```

---

## Task 2: Write resize helper with TDD

**Files:**
- Create: `cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py`
- Create: `cmscontrib/DownloadMexicanStateFlags.py` (partial — only `_resize_to_canvas`)

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py`:

```python
#!/usr/bin/env python3

import io
import unittest

from PIL import Image

from cmscontrib.DownloadMexicanStateFlags import _resize_to_canvas


def _png_bytes(width: int, height: int, color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestResizeToCanvas(unittest.TestCase):

    def test_wider_source_fits_width_and_is_padded_vertically(self):
        # 160x40 source → fits in width, padded top/bottom to 100
        data = _png_bytes(160, 40)
        result = _resize_to_canvas(data, 160, 100)
        out = Image.open(io.BytesIO(result))
        self.assertEqual(out.size, (160, 100))

    def test_taller_source_fits_height_and_is_padded_horizontally(self):
        # 40x100 source → fits in height, padded left/right to 160
        data = _png_bytes(40, 100)
        result = _resize_to_canvas(data, 160, 100)
        out = Image.open(io.BytesIO(result))
        self.assertEqual(out.size, (160, 100))

    def test_exact_size_source_unchanged(self):
        data = _png_bytes(160, 100)
        result = _resize_to_canvas(data, 160, 100)
        out = Image.open(io.BytesIO(result))
        self.assertEqual(out.size, (160, 100))

    def test_canvas_background_is_white(self):
        # Red 160x40 image — top row of output should be white padding
        data = _png_bytes(160, 40, color=(255, 0, 0))
        result = _resize_to_canvas(data, 160, 100)
        out = Image.open(io.BytesIO(result)).convert("RGB")
        # Top-left pixel should be white (padding area)
        self.assertEqual(out.getpixel((0, 0)), (255, 255, 255))

    def test_output_is_valid_png(self):
        data = _png_bytes(80, 50)
        result = _resize_to_canvas(data, 160, 100)
        self.assertTrue(result.startswith(b"\x89PNG"))

    def test_oversized_source_is_scaled_down(self):
        # 800x500 → should fit exactly in 160x100 (same ratio)
        data = _png_bytes(800, 500)
        result = _resize_to_canvas(data, 160, 100)
        out = Image.open(io.BytesIO(result))
        self.assertEqual(out.size, (160, 100))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` (file doesn't exist yet).

- [ ] **Step 3: Create the script with only `_resize_to_canvas`**

Create `cmscontrib/DownloadMexicanStateFlags.py`:

```python
#!/usr/bin/env python3

"""Download real Mexican state flag images from Wikimedia Commons.

Fetches PNG thumbnails at 160 px width, resizes/pads to 160x100, and
saves to cmsranking/flags/ (or a custom directory) as bundled assets.

Usage:
    .venv/bin/pip install -e ".[contrib]"
    .venv/bin/python cmscontrib/DownloadMexicanStateFlags.py
    .venv/bin/python cmscontrib/DownloadMexicanStateFlags.py --states JAL OAX
    .venv/bin/python cmscontrib/DownloadMexicanStateFlags.py --output-dir /tmp/flags
"""

import argparse
import io
import logging
import os
from importlib.resources import files

import requests
from PIL import Image
from urllib.parse import quote

logger = logging.getLogger(__name__)

TARGET_WIDTH = 160
TARGET_HEIGHT = 100


def _resize_to_canvas(data: bytes, width: int, height: int) -> bytes:
    """Resize image data to fit within (width, height), padding with white."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    img.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    x = (width - img.width) // 2
    y = (height - img.height) // 2
    canvas.paste(img, (x, y), img)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cmscontrib/DownloadMexicanStateFlags.py \
        cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py
git commit -m "feat: add _resize_to_canvas helper with tests for flag downloader"
```

---

## Task 3: Complete the download script

**Files:**
- Modify: `cmscontrib/DownloadMexicanStateFlags.py` (add WIKIMEDIA_FLAGS, `_fetch_flag_png`, `download_flags`, `main`)

- [ ] **Step 1: Append the remaining code to the script**

Add the following after `_resize_to_canvas` in `cmscontrib/DownloadMexicanStateFlags.py`:

```python
from urllib.parse import quote

# Maps project state code → Wikimedia Commons SVG filename (Unicode, unencoded).
# Verified against https://commons.wikimedia.org/wiki/Category:Flags_of_states_of_Mexico
WIKIMEDIA_FLAGS: dict[str, str] = {
    "AGU": "Flag_of_Aguascalientes.svg",
    "BCA": "Flag_of_Baja_California.svg",
    "BCS": "Flag_of_Baja_California_Sur.svg",
    "CAM": "Flag_of_Campeche.svg",
    "CHH": "Flag_of_Chihuahua.svg",
    "CHI": "Flag_of_Chiapas.svg",
    "CMX": "Flag_of_Mexico_City.svg",
    "COA": "Flag_of_Coahuila.svg",
    "COL": "Flag_of_Colima.svg",
    "DUR": "Flag_of_Durango.svg",
    "GUA": "Flag_of_Guanajuato.svg",
    "GRO": "Flag_of_Guerrero.svg",
    "HID": "Flag_of_Hidalgo.svg",
    "JAL": "Flag_of_Jalisco.svg",
    "MEX": "Flag_of_the_State_of_Mexico.svg",
    "MIC": "Flag_of_Michoacán.svg",
    "MOR": "Flag_of_Morelos.svg",
    "NAY": "Flag_of_Nayarit.svg",
    "NLE": "Flag_of_Nuevo_León.svg",
    "OAX": "Flag_of_Oaxaca.svg",
    "PUE": "Flag_of_Puebla.svg",
    "QUE": "Flag_of_Querétaro.svg",
    "ROO": "Flag_of_Quintana_Roo.svg",
    "SIN": "Flag_of_Sinaloa.svg",
    "SLP": "Flag_of_San_Luis_Potosí.svg",
    "SON": "Flag_of_Sonora.svg",
    "TAB": "Flag_of_Tabasco.svg",
    "TAM": "Flag_of_Tamaulipas.svg",
    "TLA": "Flag_of_Tlaxcala.svg",
    "VER": "Flag_of_Veracruz.svg",
    "YUC": "Flag_of_Yucatán.svg",
    "ZAC": "Flag_of_Zacatecas.svg",
}

_WIKIMEDIA_FILE_PATH = (
    "https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width={width}"
)


def _fetch_flag_png(wikimedia_filename: str, width: int) -> bytes:
    """Fetch a PNG thumbnail from Wikimedia Commons via Special:FilePath redirect."""
    encoded = quote(wikimedia_filename, safe="._-")
    url = _WIKIMEDIA_FILE_PATH.format(filename=encoded, width=width)
    response = requests.get(url, timeout=30, headers={"User-Agent": "cms-flag-downloader/1.0"})
    response.raise_for_status()
    return response.content


def download_flags(output_dir: str, states: list[str] | None = None) -> None:
    """Download flag images for the given state codes (default: all 32 states)."""
    os.makedirs(output_dir, exist_ok=True)
    targets = states if states is not None else list(WIKIMEDIA_FLAGS.keys())
    failed: list[str] = []
    for code in targets:
        wikimedia_filename = WIKIMEDIA_FLAGS.get(code)
        if wikimedia_filename is None:
            logger.warning("No Wikimedia filename configured for state %s; skipping.", code)
            failed.append(code)
            continue
        try:
            raw = _fetch_flag_png(wikimedia_filename, TARGET_WIDTH)
            png = _resize_to_canvas(raw, TARGET_WIDTH, TARGET_HEIGHT)
            dest = os.path.join(output_dir, f"{code}.png")
            with open(dest, "wb") as f:
                f.write(png)
            logger.info("Saved %s (%d bytes)", dest, len(png))
        except Exception:
            logger.error("Failed to download %s (%s)", code, wikimedia_filename, exc_info=True)
            failed.append(code)
    if failed:
        logger.warning("Failed states: %s", ", ".join(failed))
        logger.warning("Check the WIKIMEDIA_FLAGS mapping for these codes and retry with --states.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    default_output = str(files("cmsranking") / "flags")
    parser = argparse.ArgumentParser(
        description="Download Mexican state flags from Wikimedia Commons."
    )
    parser.add_argument(
        "--output-dir",
        default=default_output,
        help=f"Directory to save PNG files (default: {default_output})",
    )
    parser.add_argument(
        "--states",
        nargs="+",
        metavar="CODE",
        help="State codes to download, e.g. JAL OAX. Default: all 32 states.",
    )
    args = parser.parse_args()
    download_flags(args.output_dir, args.states)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run existing tests to confirm nothing broke**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py -v
```

Expected: all 6 tests PASS (the new code doesn't affect `_resize_to_canvas`).

- [ ] **Step 3: Commit**

```bash
git add cmscontrib/DownloadMexicanStateFlags.py
git commit -m "feat: add DownloadMexicanStateFlags contrib script"
```

---

## Task 4: Run the script and verify output

**Files:**
- Replace: `cmsranking/flags/*.png`

- [ ] **Step 1: Run the script for all 32 states**

```bash
.venv/bin/python cmscontrib/DownloadMexicanStateFlags.py
```

Expected output (one line per state):
```
INFO Saved .venv/lib/python3.x/site-packages/cmsranking/flags/AGU.png (NNNN bytes)
INFO Saved .venv/lib/python3.x/site-packages/cmsranking/flags/BCA.png (NNNN bytes)
...
```

If any states fail (WARNING lines), see Step 2. If all succeed, skip to Step 3.

- [ ] **Step 2: Fix any failed states (only if failures occurred)**

Open `cmscontrib/DownloadMexicanStateFlags.py` and find the `WIKIMEDIA_FLAGS` dict. For each failed state:

1. Go to `https://commons.wikimedia.org/wiki/Category:Flags_of_states_of_Mexico` in a browser.
2. Find the correct SVG filename for the state.
3. URL-encode any accented characters (e.g. `é` → `%C3%A9`).
4. Update the mapping and rerun only the failed states:

```bash
.venv/bin/python cmscontrib/DownloadMexicanStateFlags.py --states <FAILED_CODES>
```

- [ ] **Step 3: Verify output dimensions and content**

```bash
.venv/bin/python3 - <<'EOF'
import os, struct
flags_dir = "cmsranking/flags"
bad = []
for name in sorted(os.listdir(flags_dir)):
    if not name.endswith(".png"):
        continue
    data = open(os.path.join(flags_dir, name), "rb").read()
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    if (w, h) != (160, 100):
        bad.append(f"{name}: {w}x{h}")
if bad:
    print("WRONG SIZE:", bad)
else:
    print(f"All {len(os.listdir(flags_dir))} flags are 160x100 px. OK.")
EOF
```

Expected: `All 32 flags are 160x100 px. OK.`

- [ ] **Step 4: Copy output to source tree (only if script wrote to venv site-packages)**

The script's default output is the installed package location inside the venv. The source tree files (which get committed) are under `cmsranking/flags/`. Check if they differ:

```bash
.venv/bin/python3 -c "from importlib.resources import files; print(files('cmsranking') / 'flags')"
```

If the path printed is inside `.venv/` (editable install), the script already wrote to `cmsranking/flags/` directly (editable installs resolve to the source tree). Verify:

```bash
ls -lh cmsranking/flags/JAL.png
```

If the file is dated now and is larger than 98 bytes, the source tree was updated. Otherwise copy manually:

```bash
SRC=$(.venv/bin/python3 -c "from importlib.resources import files; print(files('cmsranking') / 'flags')")
cp "$SRC"/*.png cmsranking/flags/
```

- [ ] **Step 5: Run full unit test suite to confirm no regressions**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the downloaded flag images**

```bash
git add cmsranking/flags/*.png
git commit -m "assets: replace placeholder state flag images with real Wikimedia flags (160x100 px)"
```

---

## Task 5: Update logo documentation (verification only)

**Files:**
- Possibly modify: `docs/RankingWebServer.rst`

- [ ] **Step 1: Verify the existing logo section is accurate**

`docs/RankingWebServer.rst` already contains a "Logo, flags and faces" section (lines ~57–66) that explains:
- Place `logo.png` (or other supported extension) directly in the data directory
- Supported extensions: `.png`, `.jpg`, `.gif`, `.bmp`
- Recommended resolution: 200×160

And the "Default `lib_dir` location" subsection (lines ~263–270) already explains the path. No changes are needed unless the current text is wrong or outdated.

If the section needs updating, change only what is factually incorrect. Do not restructure surrounding content.

- [ ] **Step 2: Commit if any changes were made (skip if no changes)**

```bash
git add docs/RankingWebServer.rst
git commit -m "docs: update RankingWebServer logo section for accuracy"
```
