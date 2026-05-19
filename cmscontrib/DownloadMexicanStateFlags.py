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
import time
from importlib.resources import files
from urllib.parse import quote

import requests
from PIL import Image


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


logger = logging.getLogger(__name__)

TARGET_WIDTH = 160
TARGET_HEIGHT = 100

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
    "MIC": "Bandera_del_Estado_de_Michoacán.svg",
    "MOR": "Flag_of_Morelos.svg",
    "NAY": "Flag_of_Nayarit.svg",
    "NLE": "Flag_of_Nuevo_Leon.svg",
    "OAX": "Flag_of_Oaxaca.svg",
    "PUE": "Flag_of_Puebla.svg",
    "QUE": "Flag_of_Queretaro.svg",
    "ROO": "Flag_of_Quintana_Roo.svg",
    "SIN": "Flag_of_Sinaloa.svg",
    "SLP": "Flag_of_San_Luis_Potosi.svg",
    "SON": "Flag_of_Sonora.svg",
    "TAB": "Flag_of_Tabasco.svg",
    "TAM": "Flag_of_Tamaulipas.svg",
    "TLA": "Flag_of_Tlaxcala.svg",
    "VER": "Flag_of_Veracruz.svg",
    "YUC": "Flag_of_Yucatan.svg",
    "ZAC": "Flag_of_Zacatecas.svg",
}

_WIKIMEDIA_FILE_PATH = (
    "https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width={width}"
)


def _fetch_flag_png(wikimedia_filename: str, width: int) -> bytes:
    """Fetch a PNG thumbnail from Wikimedia Commons via Special:FilePath redirect."""
    encoded = quote(wikimedia_filename, safe="._-")
    url = _WIKIMEDIA_FILE_PATH.format(filename=encoded, width=width)
    for attempt in range(5):
        response = requests.get(url, timeout=30, headers={"User-Agent": "cms-flag-downloader/1.0"})
        if response.status_code == 429:
            wait = 10 * (2 ** attempt)
            logger.warning("Rate limited; retrying %s in %ds (attempt %d/5)...",
                           wikimedia_filename, wait, attempt + 1)
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.content
    response.raise_for_status()
    return response.content  # unreachable, satisfies type checker


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
