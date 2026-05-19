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

    def test_rgba_source_blends_alpha_onto_white(self):
        # Semi-transparent red pixel on transparent background →
        # result should be pinkish (blended with white), not pure red or pure white
        img = Image.new("RGBA", (160, 100), (255, 0, 0, 128))  # 50% transparent red
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = _resize_to_canvas(buf.getvalue(), 160, 100)
        out = Image.open(io.BytesIO(result)).convert("RGB")
        r, g, b = out.getpixel((80, 50))
        # Should be between pure white (255,255,255) and pure red (255,0,0)
        self.assertGreater(r, 200)  # reddish
        self.assertGreater(g, 100)  # blended with white
        self.assertGreater(b, 100)  # blended with white


if __name__ == "__main__":
    unittest.main()
