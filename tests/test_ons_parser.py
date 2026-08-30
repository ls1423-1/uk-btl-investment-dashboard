from __future__ import annotations

import unittest

import pandas as pd

from fetch_real_data import _canonical_type, _discover_measure_table


HEADERS = [
    "Time period", "Area code", "Area name", "Region or country name",
    "Index", "Annual change", "Rental price",
    "Index one bed", "Annual change one bed", "Rental price one bed",
    "Index two bed", "Annual change two bed", "Rental price two bed",
    "Index three bed", "Annual change three bed", "Rental price three bed",
    "Index four or more bed", "Annual change four or more bed", "Rental price four or more bed",
    "Index detached", "Annual change detached", "Rental price detached",
    "Index semidetached", "Annual change semidetached", "Rental price semidetached",
    "Index terraced", "Annual change terraced", "Rental price terraced",
    "Index flat maisonette", "Annual change flat maisonette", "Rental price flat maisonette",
]


def _observation(date: str, code: str, area: str, base: int) -> list[object]:
    row: list[object] = [date, code, area, "Example region"]
    for offset in range(9):
        row.extend([100.0 + offset, 4.0 + offset / 10, base + offset * 100])
    return row


class OnsParserTests(unittest.TestCase):
    def test_measure_family_layout_retains_all_property_and_bedroom_histories(self) -> None:
        raw = pd.DataFrame([
            ["Price Index of Private Rents"] + [None] * (len(HEADERS) - 1),
            ["Notes"] + [None] * (len(HEADERS) - 1),
            HEADERS,
            _observation("2025-01-01", "E06000001", "Example", 800),
            _observation("2025-02-01", "E06000001", "Example", 810),
        ])

        rents, bedrooms = _discover_measure_table(raw)

        self.assertIsNotNone(rents)
        self.assertIsNotNone(bedrooms)
        assert rents is not None
        assert bedrooms is not None
        self.assertEqual(len(rents), 10)
        self.assertEqual(len(bedrooms), 8)
        self.assertEqual(set(rents["property_type"]), {
            "Overall", "Detached", "Semi-detached", "Terraced", "Flat/maisonette"
        })
        self.assertEqual(set(bedrooms["bedrooms"]), {"1", "2", "3", "4+"})
        self.assertEqual(rents["date"].min(), pd.Timestamp("2025-01-01"))
        self.assertEqual(rents["date"].max(), pd.Timestamp("2025-02-01"))
        self.assertTrue(rents["rent_index"].notna().all())
        self.assertTrue(rents["annual_rent_change"].notna().all())
        self.assertTrue(rents["observation_status"].eq("observed").all())

    def test_semidetached_is_not_classified_as_detached(self) -> None:
        self.assertEqual(_canonical_type("Rental price semidetached"), "Semi-detached")
        self.assertEqual(_canonical_type("Rental price detached"), "Detached")


if __name__ == "__main__":
    unittest.main()
