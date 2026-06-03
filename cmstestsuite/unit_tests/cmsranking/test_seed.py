#!/usr/bin/env python3

import os
import shutil
import struct
import tempfile
import unittest
import zlib
from importlib.resources import files
from unittest.mock import MagicMock, patch

from cmsranking.seed import _copy_bundled_flags, _register_teams_from_flags, seed_faces, seed_logo
from cmsranking.Store import Store
from cmsranking.Team import Team


def _make_png(r=100, g=100, b=200, w=4, h=4):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    row = bytes([0] + [r, g, b] * w)
    idat = chunk(b"IDAT", zlib.compress(row * h))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _make_team_store(base_dir):
    stores = {}
    store = Store(Team, os.path.join(base_dir, "teams"), stores)
    store.load_from_disk()
    return store


class TestRegisterTeamsFromFlags(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.flags_dir = os.path.join(self._tmp, "flags")
        os.makedirs(self.flags_dir)
        self.team_store = _make_team_store(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def _add_image(self, filename):
        with open(os.path.join(self.flags_dir, filename), "wb") as f:
            f.write(_make_png())

    def test_creates_team_for_known_mx_state(self):
        self._add_image("JAL.png")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("JAL", self.team_store)
        self.assertEqual(self.team_store.retrieve("JAL")["name"], "Jalisco")

    def test_creates_team_for_unknown_code_uses_filename(self):
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
        with open(os.path.join(self.flags_dir, "README.txt"), "w") as f:
            f.write("not an image")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertNotIn("README", self.team_store)

    def test_deduplicates_same_stem_different_extensions(self):
        for ext in (".png", ".jpg", ".gif", ".bmp"):
            self._add_image(f"DUP{ext}")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("DUP", self.team_store)
        # Only one team created for stem "DUP"
        self.assertEqual(self.team_store.retrieve("DUP")["name"], "DUP")

    def test_registers_multiple_distinct_images(self):
        for code in ("AGU", "BCA", "CUSTOM"):
            self._add_image(f"{code}.png")
        _register_teams_from_flags(self.flags_dir, self.team_store)
        self.assertIn("AGU", self.team_store)
        self.assertIn("BCA", self.team_store)
        self.assertIn("CUSTOM", self.team_store)


class TestCopyBundledFlags(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.flags_dir = os.path.join(self._tmp, "flags")
        os.makedirs(self.flags_dir)

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def test_copies_all_bundled_flags(self):
        _copy_bundled_flags(self.flags_dir)
        copied = sorted(os.listdir(self.flags_dir))
        bundled = files("cmsranking") / "flags"
        expected = sorted(r.name for r in bundled.iterdir() if r.is_file())
        self.assertCountEqual(copied, expected)
        self.assertIn("JAL.png", copied)

    def test_overwrites_existing_file(self):
        target = os.path.join(self.flags_dir, "JAL.png")
        with open(target, "wb") as f:
            f.write(b"old content")
        _copy_bundled_flags(self.flags_dir)
        with open(target, "rb") as f:
            content = f.read()
        self.assertNotEqual(content, b"old content")
        self.assertTrue(content.startswith(b"\x89PNG"))

    def test_only_copies_image_files(self):
        _copy_bundled_flags(self.flags_dir)
        for name in os.listdir(self.flags_dir):
            ext = os.path.splitext(name)[1].lower()
            self.assertIn(ext, {".png", ".jpg", ".jpeg", ".gif", ".bmp"})


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


if __name__ == "__main__":
    unittest.main()
