import re
import unittest
from open_notebook.utils.text_utils import clean_markdown_content


class TestCleanMarkdown(unittest.TestCase):
    def test_clean_markdown_removes_empty_table_rows(self):
        content = """# Title
| | | | | | |
| | | | | | |
| Col 1 | Col 2 |
| | | | | | |
| Val 1 | Val 2 |
| | | | | | |
"""
        cleaned = clean_markdown_content(content)
        self.assertIn("| Col 1 | Col 2 |", cleaned)
        self.assertIn("| Val 1 | Val 2 |", cleaned)
        self.assertNotIn("| | | | | | |", cleaned)
        self.assertIn("| --- | --- |", cleaned)

    def test_clean_markdown_compresses_sparse_rows(self):
        content = """| | | | Val A | Val B | | | |"""
        cleaned = clean_markdown_content(content)
        self.assertIn("| Val A | Val B |", cleaned)
        self.assertIn("| --- | --- |", cleaned)

    def test_clean_markdown_preserves_normal_tables(self):
        content = """| Name | Age | City |
| --- | --- | --- |
| Alice | 30 | Tokyo |
| Bob | 25 | Hanoi |"""
        cleaned = clean_markdown_content(content)
        self.assertEqual(cleaned.strip(), content.strip())

    def test_clean_markdown_formats_steps_as_headings(self):
        content = """| 1. Init | | | |
| Setup environment | | | |
| 2. Run | | | |"""
        cleaned = clean_markdown_content(content)
        self.assertIn("### 1. Init", cleaned)
        self.assertIn("Setup environment", cleaned)
        self.assertIn("### 2. Run", cleaned)

    def test_clean_markdown_preserves_code_blocks(self):
        content = """```python
flags = 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256
| | | | | | | | | | | |
```"""
        cleaned = clean_markdown_content(content)
        self.assertEqual(cleaned.strip(), content.strip())


    def test_clean_markdown_preserves_sparse_table_without_shredding(self):
        content = """| No | Case ID | Step | Expected | Result |
| --- | --- | --- | --- | --- |
| 1 | TC-001 | Click login | Success | Pass |
| 2 |  | Check DB |  |  |
| 3 |  | Check Log | Expected log | Pass |"""
        cleaned = clean_markdown_content(content)
        # Table must remain 1 table without extra headers or shredding
        sep_lines = [l for l in cleaned.splitlines() if re.match(r"^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$", l.strip())]
        self.assertEqual(len(sep_lines), 1)
        self.assertIn("| 1 | TC-001 | Click login | Success | Pass |", cleaned)
        self.assertIn("| 2 |  | Check DB |  |  |", cleaned)
        self.assertIn("| 3 |  | Check Log | Expected log | Pass |", cleaned)

    def test_clean_markdown_pads_short_rows_in_valid_table(self):
        content = """| Col A | Col B | Col C |
| --- | --- | --- |
| Val 1 | Val 2 |
| Val 3 |"""
        cleaned = clean_markdown_content(content)
        sep_lines = [l for l in cleaned.splitlines() if re.match(r"^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$", l.strip())]
        self.assertEqual(len(sep_lines), 1)
        self.assertIn("| Val 1 | Val 2 |  |", cleaned)
        self.assertIn("| Val 3 |  |  |", cleaned)


if __name__ == "__main__":
    unittest.main()

