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
from urllib.parse import quote

import requests
from PIL import Image

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
