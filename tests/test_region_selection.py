import unittest
from pathlib import Path

from PIL import Image

try:
    from medical_image_locator_report_identify.app import ImageRecord, choose_best_regions, make_image_id
    from medical_image_locator_report_identify.coordinate_utils import Box
    from medical_image_locator_report_identify.model_client import LocalizationResult
    from medical_image_locator_report_identify.report_extractor import ReportTerm
except ModuleNotFoundError:
    from app import ImageRecord, choose_best_regions, make_image_id
    from coordinate_utils import Box
    from model_client import LocalizationResult
    from report_extractor import ReportTerm


class RegionSelectionTests(unittest.TestCase):
    def test_make_image_id_is_stable_study_order_id(self):
        self.assertEqual(make_image_id(0), "IMG-001")
        self.assertEqual(make_image_id(12), "IMG-013")

    def test_choose_best_regions_uses_confidence_then_area(self):
        image = Image.new("RGB", (100, 100))
        records = [
            ImageRecord(
                path=Path("img1.png"),
                image=image,
                image_id="IMG-001",
                results={
                    "tumor": LocalizationResult(
                        mode="box_tool",
                        answer="",
                        confidence=0.9,
                        pixel_box=Box(10, 10, 20, 20),
                    )
                },
            ),
            ImageRecord(
                path=Path("img2.png"),
                image=image,
                image_id="IMG-002",
                results={
                    "tumor": LocalizationResult(
                        mode="box_tool",
                        answer="",
                        confidence=0.7,
                        pixel_box=Box(10, 10, 80, 80),
                    )
                },
            ),
        ]

        best = choose_best_regions(records, [ReportTerm(name="tumor")])
        self.assertEqual(best["tumor"].image_id, "IMG-001")
        self.assertGreater(best["tumor"].score, 0)

    def test_choose_best_regions_uses_area_when_confidence_is_missing(self):
        image = Image.new("RGB", (100, 100))
        records = [
            ImageRecord(
                path=Path("img1.png"),
                image=image,
                image_id="IMG-001",
                results={"cistern": LocalizationResult(mode="box_tool", answer="", pixel_box=Box(10, 10, 20, 20))},
            ),
            ImageRecord(
                path=Path("img2.png"),
                image=image,
                image_id="IMG-002",
                results={"cistern": LocalizationResult(mode="box_tool", answer="", pixel_box=Box(10, 10, 50, 50))},
            ),
        ]

        best = choose_best_regions(records, [ReportTerm(name="cistern")])
        self.assertEqual(best["cistern"].image_id, "IMG-002")


if __name__ == "__main__":
    unittest.main()
