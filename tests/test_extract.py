"""
Tests for engine/extract.py, run against the synthetic PDFs in tests/fixtures/.

Run (from the repo root, with pypdf and cryptography installed):
    python tests/make_fixtures.py
    python -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import extract  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def inspect(name):
    out = json.loads(extract.run_json((FIXTURES / name).read_bytes()))
    assert out["ok"], out
    return out["report"]


class SchemaTests(unittest.TestCase):
    def test_engine_keys_match_fields_json(self):
        schema = json.loads((ROOT / "engine" / "fields.json").read_text(encoding="utf-8"))
        self.assertEqual([f["key"] for f in schema["fields"]], extract.FIELD_KEYS)

    def test_every_field_has_the_same_shape(self):
        for name in ("full.pdf", "plain.pdf", "owner-locked.pdf", "user-locked.pdf"):
            report = inspect(name)
            self.assertEqual(list(report["fields"]), extract.FIELD_KEYS, name)
            for key, field in report["fields"].items():
                self.assertEqual(set(field), {"status", "summary", "detail", "notes", "flags", "data"}, (name, key))
                self.assertIn(field["status"], {"found", "absent", "error", "locked"}, (name, key))
                if field["status"] == "found":
                    self.assertTrue(field["summary"], (name, key))


class FullFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = inspect("full.pdf")
        cls.f = cls.report["fields"]

    def test_nothing_errored(self):
        errors = {k: v["notes"] for k, v in self.f.items() if v["status"] == "error"}
        self.assertEqual(errors, {})

    def test_document_metadata(self):
        self.assertEqual(self.f["title"]["summary"], "Riverside Clinic - Level 1 Floor Plan")
        self.assertEqual(self.f["creator"]["summary"], "Autodesk Revit 2025")
        self.assertEqual(self.f["producer"]["summary"], "Bluebeam PDF Library 21")
        self.assertEqual(self.f["created"]["summary"], "2026-03-01 09:30:00 -08:00")
        self.assertEqual(self.f["page_count"]["data"], {"count": 3})

    def test_mixed_page_sizes_are_flagged(self):
        sizes = self.f["page_sizes"]
        self.assertTrue(sizes["summary"].startswith("Mixed"))
        self.assertTrue(sizes["flags"])
        self.assertEqual([d["sheet"] for d in sizes["data"]], ["ARCH D", "Letter"])

    def test_layers_and_flags(self):
        self.assertEqual(self.f["layers"]["detail"][0], "Level 1 Floor Plan › A-WALL")
        flags = {r["name"]: r for r in self.f["layer_flags"]["data"]}
        self.assertTrue(flags["A-WALL"]["locked"])
        self.assertEqual(flags["A-DOOR"]["print"], "off")
        self.assertFalse(flags["G-ANNO-TEXT"]["visible"])

    def test_tagged_objects_are_structured(self):
        data = self.f["tagged_objects"]["data"]
        self.assertEqual(data["objectCount"], 2)
        self.assertEqual(data["objects"][0]["properties"]["Family"], "Single-Flush")
        self.assertEqual(data["objects"][1]["properties"]["Category"], "Walls")  # via ClassMap
        self.assertEqual(data["fieldNames"][:4], ["Category", "Family", "Type", "Level"])

    def test_bluebeam(self):
        self.assertEqual(self.f["bb_custom_properties"]["data"], {"Project Number": "2026-118", "Phase": "Construction Documents"})
        self.assertEqual(self.f["bb_custom_columns"]["data"]["columns"], ["Status", "Discipline"])
        self.assertIn("Status: Open", self.f["bb_custom_columns"]["detail"][0])
        values = sorted(m["value"] for m in self.f["bb_measurements"]["data"])
        self.assertEqual(values, ["1,245.50 sf", "42'-6\""])

    def test_scale_and_calibration_feed(self):
        scale = self.f["embedded_scale"]["data"][0]
        self.assertEqual(scale["ratio"], "1/8 in = 1 ft")
        self.assertAlmostEqual(scale["unitsPerPoint"], 8 / 72, places=6)
        calibration = self.report["calibration"]
        self.assertEqual(calibration["scale"][0]["unit"], "ft")
        self.assertEqual(len(calibration["pages"]), 3)

    def test_geopdf_both_encodings(self):
        geo = self.f["geopdf"]["data"]
        self.assertEqual(geo[0]["epsg"], 2230)
        self.assertEqual(geo[0]["coordinateSystem"], "NAD83 / California zone 6 (ftUS)")
        self.assertEqual(len(geo[0]["geoPoints"]), 4)
        self.assertEqual(geo[1]["source"], "LGIDict (older GeoPDF encoding)")

    def test_navigation(self):
        self.assertEqual(self.f["bookmarks"]["data"][1], {"title": "A101 Floor Plan", "page": 1, "level": 1})
        links = self.f["hyperlinks"]["data"]
        self.assertIn({"type": "internal", "page": 1, "target": "p.2", "count": 2}, links)
        self.assertIn({"type": "external", "page": 2, "target": "https://bim-press.com", "count": 1}, links)
        self.assertEqual(self.f["form_fields"]["summary"], "2 fields, 1 filled in")
        self.assertIn("“Verify door swing”", self.f["annotations"]["detail"][0])

    def test_embedded_assets(self):
        attachment = self.f["attachments"]["data"][0]
        self.assertEqual(attachment["name"], "door-schedule.csv")
        self.assertEqual(attachment["bytes"], 44)
        fonts = {f["name"]: f for f in self.f["fonts"]["data"]}
        self.assertTrue(fonts["ArialMT"]["embedded"] and fonts["ArialMT"]["subset"])
        self.assertTrue(fonts["Helvetica"]["standard14"])
        self.assertIn("RomanS", self.f["fonts"]["flags"][0])
        images = self.f["images"]["data"]
        self.assertEqual(len(images), 2)  # the soft mask is not counted
        self.assertEqual(images[1]["pages"], [1, 2])  # shared resources credited to both pages
        self.assertEqual(images[1]["encoding"], "JPEG")

    def test_not_encrypted(self):
        self.assertEqual(self.f["encryption"]["status"], "absent")
        self.assertEqual(self.f["permissions"]["status"], "absent")


class PlainAndSecurityTests(unittest.TestCase):
    def test_plain_file_is_mostly_absent(self):
        f = inspect("plain.pdf")["fields"]
        found = {k for k, v in f.items() if v["status"] == "found"}
        # pypdf stamps /Producer on everything it writes.
        self.assertEqual(found, {"producer", "page_count", "page_sizes", "page_units"})
        self.assertIsNone(inspect("plain.pdf")["calibration"])

    def test_owner_password_file_is_readable_with_restrictions(self):
        report = inspect("owner-locked.pdf")
        f = report["fields"]
        self.assertFalse(report["locked"])
        self.assertEqual(f["title"]["summary"], "Owner-protected sheet")
        self.assertIn("AES 256-bit", f["encryption"]["summary"])
        perms = f["permissions"]["data"]["permissions"]
        self.assertEqual(perms["Printing"], "not allowed")
        self.assertEqual(perms["Copying text and graphics"], "not allowed")
        self.assertEqual(perms["Commenting"], "allowed")

    def test_open_password_file_is_locked_not_crashed(self):
        report = inspect("user-locked.pdf")
        self.assertTrue(report["locked"])
        self.assertIn("AES 128-bit", report["fields"]["encryption"]["summary"])
        self.assertEqual(report["fields"]["title"]["status"], "locked")

    def test_non_pdf_returns_a_message(self):
        out = json.loads(extract.run_json((FIXTURES / "not-a-pdf.pdf").read_bytes()))
        self.assertFalse(out["ok"])
        self.assertIn("couldn't be read as a PDF", out["message"])

    def test_progress_callback_is_called(self):
        seen = []
        extract.run_json((FIXTURES / "full.pdf").read_bytes(), lambda label, frac: seen.append(frac))
        self.assertTrue(seen)
        self.assertTrue(all(0 <= x <= 1 for x in seen))


if __name__ == "__main__":
    unittest.main()
