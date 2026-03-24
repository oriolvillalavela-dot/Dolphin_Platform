import unittest

import pandas as pd

from blueprints.screenings.api import _parse_eln_raw_dataframe


class TestScreeningsElnParser(unittest.TestCase):
    def test_parses_three_blocks_with_multiple_sm_and_prod(self):
        raw_rows = [
            # 1) Metadata header
            ["experiment_name", "user_name", "date_created_full_char", "theme_number", "project_id", "project_name"],
            # 2) Metadata row
            ["EXP-1001", "chemist_1", "2026-03-19 08:30:00", "T-21", "PRJ-21", "Project Atlas"],
            # 3) Blank
            ["", "", "", "", "", ""],
            # 4) SM header
            ["reactant_name", "smiles", "equivalents", "mmol", "mmol_units"],
            # 5) SM rows
            ["Main substrate", "C1=CC=CC=C1", "1", "36.9337593", "umol"],
            ["triethylsilane", "CC[SiH](CC)CC", "3", "110.8", "umol"],
            ["TFA", "OC(=O)C(F)(F)F", "2", "73.86", "umol"],
            # 6) Blank
            ["", "", "", "", ""],
            # 7) Product header
            ["product_name", "smiles"],
            # 8) Product rows
            ["Product 1", "CCOC(=O)N"],
            ["Product 2", "CCN"],
        ]

        parsed = _parse_eln_raw_dataframe(pd.DataFrame(raw_rows))
        md = parsed["metadata"]

        self.assertEqual(md["eln_id"], "EXP-1001")
        self.assertEqual(md["user"], "chemist_1")
        self.assertEqual(md["date"], "2026-03-19T08:30:00")
        self.assertEqual(md["theme_number"], "T-21")
        self.assertEqual(md["project_id"], "PRJ-21")
        self.assertEqual(md["project_name"], "Project Atlas")
        self.assertEqual(md["scale_value"], "36.9337593")
        self.assertEqual(md["scale_units"], "umol")
        self.assertEqual(len(parsed["eln_stmat_data"]), 3)
        self.assertEqual(len(parsed["eln_product_data"]), 2)

    def test_scale_falls_back_to_first_sm_if_no_equivalent_one(self):
        raw_rows = [
            ["experiment_name", "user_name", "date_created_full_char", "theme_number", "project_id", "project_name"],
            ["EXP-1002", "chemist_2", "2026-03-20", "T-22", "PRJ-22", "Project Borealis"],
            ["", "", "", "", "", ""],
            ["reactant_name", "smiles", "equivalents", "mmol", "mmol_units"],
            ["SM-1", "CCO", "2", "0.50", "mmol"],
            ["SM-2", "CCN", "3", "0.75", "mmol"],
            ["", "", "", "", ""],
            ["product_name", "smiles"],
            ["P-1", "CCC"],
        ]

        parsed = _parse_eln_raw_dataframe(pd.DataFrame(raw_rows))
        md = parsed["metadata"]

        self.assertEqual(md["scale_value"], "0.50")
        self.assertEqual(md["scale_units"], "mmol")
        self.assertEqual(len(parsed["eln_stmat_data"]), 2)
        self.assertEqual(len(parsed["eln_product_data"]), 1)


if __name__ == "__main__":
    unittest.main()

