import unittest

from medical_image_locator.coordinate_utils import (
    Box,
    clamp_model_coord,
    denormalize_box,
    denormalize_point,
    normalize_box,
)


class CoordinateUtilsTests(unittest.TestCase):
    def test_clamp_model_coord(self):
        self.assertEqual(clamp_model_coord(-5), 0)
        self.assertEqual(clamp_model_coord(1005), 999)
        self.assertEqual(clamp_model_coord(500.4), 500)

    def test_denormalize_point_uses_1000_grid(self):
        self.assertEqual(denormalize_point(500, 500, 1280, 720), (640, 360))
        self.assertEqual(denormalize_point(999, 999, 1280, 720), (1278, 719))

    def test_normalize_box_sorts_corners(self):
        self.assertEqual(normalize_box(800, 700, 200, 100), Box(200, 100, 800, 700))

    def test_denormalize_box(self):
        self.assertEqual(denormalize_box(Box(250, 250, 750, 750), 1000, 800), Box(250, 200, 750, 600))


if __name__ == "__main__":
    unittest.main()
