import unittest
from open_notebook.utils.text_utils import clean_markdown_content
import openpyxl
import tempfile
import os
import asyncio
from open_notebook.utils.document_ocr import extract_xlsx_with_images


class TestExcelTableExtraction(unittest.TestCase):
    def test_clean_markdown_does_not_merge_pipe_blockquote_into_table(self):
        """Blockquotes or code lines containing pipes should not swallow subsequent headers."""
        text = (
            "> 📌 **[Query Info]**:\n"
            "> requests\n"
            "> | summarize count() by operation\n"
            "> | order by count() desc\n\n"
            "> 📌 **[Section 1: Test Execution]**\n\n"
            "| No | Scenario | Case | Result |\n"
            "| --- | --- | --- | --- |\n"
            "| 1 | S1 | 1_1 | OK |\n"
            "| 2 | S1 | 1_2 | OK |\n"
        )
        cleaned = clean_markdown_content(text)
        self.assertIn("> | summarize count() by operation", cleaned)
        self.assertIn("> 📌 **[Section 1: Test Execution]**", cleaned)
        self.assertIn("| No | Scenario | Case | Result |", cleaned)
        self.assertIn("| 1 | S1 | 1_1 | OK |", cleaned)
        self.assertIn("| 2 | S1 | 1_2 | OK |", cleaned)

    def test_vertical_merge_does_not_duplicate_text_and_preserves_columns(self):
        """Vertically merged cells should NOT repeat data across child rows, preventing context bloat."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "TestCases"

        # Headers at row 1
        headers = ["No", "ScenarioID", "CaseNo", "Operation", "ExpectedResult"]
        for c, h in enumerate(headers, 1):
            ws.cell(row=1, column=c, value=h)

        # Row 2 (Parent)
        ws.cell(row=2, column=1, value="1")
        ws.cell(row=2, column=2, value="SCENARIO_AUTH")
        ws.cell(row=2, column=3, value="TC_01")
        ws.cell(row=2, column=4, value="Click login button")
        ws.cell(row=2, column=5, value="Redirect to dashboard")

        # Row 3 (Child row in vertical merge: ScenarioID and CaseNo merged from row 2 to 3)
        ws.cell(row=3, column=1, value="2")
        ws.cell(row=3, column=4, value="Verify session cookie")
        ws.cell(row=3, column=5, value="Cookie exists")

        # Merge ScenarioID (B2:B3) and CaseNo (C2:C3)
        ws.merge_cells("B2:B3")
        ws.merge_cells("C2:C3")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            extracted = asyncio.run(extract_xlsx_with_images(tmp_path))
            cleaned = clean_markdown_content(extracted)

            # Row 1 has parent data; Row 2 does NOT duplicate parent text but preserves exact columns
            self.assertIn("| 1 | SCENARIO_AUTH | TC_01 | Click login button | Redirect to dashboard |", cleaned)
            self.assertIn("| 2 |  |  | Verify session cookie | Cookie exists |", cleaned)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_hierarchical_multi_level_header(self):
        """2-level headers with horizontal merges should be combined into 'Group > Subgroup'."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Timeline"

        # Row 1: Group headers
        ws.cell(row=1, column=1, value="Time")
        ws.cell(row=1, column=2, value="Open Valve")
        ws.cell(row=1, column=4, value="Close Valve")
        ws.merge_cells("B1:C1")
        ws.merge_cells("D1:E1")

        # Row 2: Sub-headers
        ws.cell(row=2, column=2, value="API Request")
        ws.cell(row=2, column=3, value="Batch Intake")
        ws.cell(row=2, column=4, value="API Request")
        ws.cell(row=2, column=5, value="Batch Intake")

        # Row 3: Data
        ws.cell(row=3, column=1, value="09:00")
        ws.cell(row=3, column=2, value="IF01")
        ws.cell(row=3, column=3, value="BF01")
        ws.cell(row=3, column=4, value="IF02")
        ws.cell(row=3, column=5, value="BF02")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            extracted = asyncio.run(extract_xlsx_with_images(tmp_path))
            cleaned = clean_markdown_content(extracted)

            self.assertIn("Open Valve > API Request", cleaned)
            self.assertIn("Open Valve > Batch Intake", cleaned)
            self.assertIn("Close Valve > API Request", cleaned)
            self.assertIn("Close Valve > Batch Intake", cleaned)
            self.assertIn("| 09:00 | IF01 | BF01 | IF02 | BF02 |", cleaned)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_pipe_escaping_in_cells(self):
        """Pipes in cell values must be escaped so they do not break Markdown table structure."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "RegexRules"

        ws.cell(row=1, column=1, value="Rule ID")
        ws.cell(row=1, column=2, value="Pattern")
        ws.cell(row=2, column=1, value="R01")
        ws.cell(row=2, column=2, value="cat|dog|fish")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            extracted = asyncio.run(extract_xlsx_with_images(tmp_path))
            cleaned = clean_markdown_content(extracted)

            self.assertIn(r"cat\|dog\|fish", cleaned)
            # Table should have exactly 2 columns, not 4
            self.assertIn(r"| R01 | cat\|dog\|fish |", cleaned)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_banner_row_callout(self):
        """Wide banner rows should be emitted as callouts, not as malformed 100-column tables."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Scenarios"

        ws.cell(row=1, column=1, value="No")
        ws.cell(row=1, column=2, value="Name")
        ws.cell(row=2, column=1, value="1")
        ws.cell(row=2, column=2, value="Alpha")

        # Full-width banner at row 3 (spanning 20 columns)
        ws.cell(row=3, column=1, value="Section B: Advanced Test Scenarios")
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=20)

        # Row 4: Next data row continuing schema
        ws.cell(row=4, column=1, value="2")
        ws.cell(row=4, column=2, value="Beta")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            extracted = asyncio.run(extract_xlsx_with_images(tmp_path))
            cleaned = clean_markdown_content(extracted)

            self.assertIn("> 📌 **[Section B: Advanced Test Scenarios]**", cleaned)
            self.assertIn("| 1 | Alpha |", cleaned)
            self.assertIn("| 2 | Beta |", cleaned)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_shape_deduplication_and_timeline_consolidation(self):
        """Diagram shapes should be deduplicated with counts, timeline ticks consolidated, and location header grouped."""
        from unittest.mock import patch

        fake_drawings = {
            "Sheet1": [
                {"type": "shape", "sort_key": (1, 0), "loc_desc": "khu vực ô A2:U43", "text": "09:00"},
                {"type": "shape", "sort_key": (1, 1), "loc_desc": "khu vực ô A2:U43", "text": "09:05"},
                {"type": "shape", "sort_key": (1, 2), "loc_desc": "khu vực ô A2:U43", "text": "09:10"},
                {"type": "shape", "sort_key": (1, 3), "loc_desc": "khu vực ô A2:U43", "text": "[Màu vàng] BF002"},
                {"type": "shape", "sort_key": (1, 4), "loc_desc": "khu vực ô A2:U43", "text": "[Màu vàng] BF002"},
                {"type": "shape", "sort_key": (1, 5), "loc_desc": "khu vực ô A2:U43", "text": "[Màu vàng] BF002"},
                {"type": "shape", "sort_key": (1, 6), "loc_desc": "khu vực ô A2:U43", "text": "Single Note"},
            ]
        }
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.cell(row=1, column=1, value="Data")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            with patch("open_notebook.utils.document_ocr._extract_xlsx_drawings", return_value=fake_drawings):
                extracted = asyncio.run(extract_xlsx_with_images(tmp_path))
                cleaned = clean_markdown_content(extracted)

                # Timeline ticks should be combined into one line
                self.assertIn("⏱️ **Mốc thời gian:** 09:00 | 09:05 | 09:10", cleaned)
                # Repeated shapes should be aggregated with count
                self.assertIn("[Màu vàng] BF002 *(lặp lại 3 lần)*", cleaned)
                # Single item under same anchor should be bulleted
                self.assertIn("Single Note", cleaned)
                # Location header should appear once
                self.assertEqual(cleaned.count("khu vực ô A2:U43"), 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
