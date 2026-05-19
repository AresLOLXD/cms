#!/usr/bin/env python3

"""Seed ranking server with bundled flag images and auto-registered teams."""

import logging
import os
from importlib.resources import files

from cmsranking.Entity import InvalidData
from cmsranking.mx_states import MX_STATES
from cmsranking.Store import Store
from cmsranking.Team import Team

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}


def _copy_bundled_flags(flags_dir: str) -> None:
    """Copy all bundled flag images to flags_dir, overwriting existing files."""
    bundled = files("cmsranking") / "flags"
    resources = [
        r for r in bundled.iterdir()
        if r.is_file() and os.path.splitext(r.name)[1].lower() in _IMAGE_EXTS
    ]
    if not resources:
        logger.warning("No bundled flag images found; check package installation.")
        return
    for resource in resources:
        dest = os.path.join(flags_dir, resource.name)
        with open(dest, "wb") as f:
            f.write(resource.read_bytes())


def _register_teams_from_flags(flags_dir: str, team_store: Store) -> None:
    """Create a team for each unique image stem in flags_dir not yet registered."""
    seen: set[str] = set()
    for filename in os.listdir(flags_dir):
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTS:
            continue
        if stem in seen or stem in team_store:
            continue
        seen.add(stem)
        name = MX_STATES.get(stem, stem)
        try:
            team_store.create(stem, {"name": name})
            logger.info("Registered team '%s' (%s) from flag image.", stem, name)
        except (InvalidData, OSError):
            logger.warning("Could not register team '%s'.", stem, exc_info=True)


def seed_flags_and_teams(lib_dir: str, team_store: Store) -> None:
    """Copy bundled flags and auto-register teams on ranking server startup."""
    flags_dir = os.path.join(lib_dir, "flags")
    os.makedirs(flags_dir, exist_ok=True)
    _copy_bundled_flags(flags_dir)
    _register_teams_from_flags(flags_dir, team_store)
