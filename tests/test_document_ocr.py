import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

from open_notebook.utils.document_ocr import (
    classify_color_hex,
    extract_docx_with_images,
    extract_document_content_hybrid,
    extract_pdf_with_images,
    extract_pptx_with_images,
    extract_xlsx_with_images,
    format_image_ocr_callout,
    is_document_with_images_supported,
)


class TestDocumentOCR(unittest.IsolatedAsyncioTestCase):
    def test_is_document_with_images_supported(self):
        self.assertTrue(is_document_with_images_supported("file.docx"))
        self.assertTrue(is_document_with_images_supported("file.PPTX"))
        self.assertTrue(is_document_with_images_supported("file.xlsx"))
        self.assertTrue(is_document_with_images_supported("file.pdf"))
        self.assertFalse(is_document_with_images_supported("file.txt"))
        self.assertFalse(is_document_with_images_supported("file.png"))
        self.assertFalse(is_document_with_images_supported("file.mp3"))
        self.assertTrue(
            is_document_with_images_supported(
                "unknown",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        )
        self.assertTrue(
            is_document_with_images_supported(
                "unknown", mime="application/pdf"
            )
        )

    def test_format_image_ocr_callout(self):
        callout = format_image_ocr_callout("Slide 3: Kiến trúc", "Line 1\nLine 2")
        self.assertIn("> 🖼️ **[Hình ảnh tại: Slide 3: Kiến trúc]**", callout)
        self.assertIn("> Line 1", callout)
        self.assertIn("> Line 2", callout)

    async def test_extract_docx_with_images(self):
        from docx import Document

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test.docx"
            doc = Document()
            doc.add_heading("1. Giới thiệu hệ thống", level=1)
            doc.add_paragraph("Đoạn văn mở đầu mô tả tài liệu.")

            # Create dummy image
            img = Image.new("RGB", (150, 80), color=(255, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            doc.add_picture(buf)

            doc.add_paragraph("Đoạn văn sau hình ảnh sơ đồ.")
            doc.save(str(file_path))

            with patch(
                "open_notebook.utils.document_ocr.ocr_image_bytes",
                AsyncMock(return_value="| Bước | Thao tác |\n| 1 | Bắt đầu |"),
            ):
                content = await extract_docx_with_images(str(file_path))
                self.assertIn("# 1. Giới thiệu hệ thống", content)
                self.assertIn("Đoạn văn mở đầu", content)
                self.assertIn("Đoạn văn sau hình ảnh", content)
                self.assertIn("Hình ảnh tại: 1. Giới thiệu hệ thống", content)
                self.assertIn("| Bước | Thao tác |", content)

    async def test_extract_pptx_with_images(self):
        from pptx import Presentation
        from pptx.util import Inches

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test.pptx"
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = "Tổng quan kiến trúc"
            slide.placeholders[1].text = "Nội dung thuyết trình slide"

            img = Image.new("RGB", (150, 80), color=(0, 255, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            slide.shapes.add_picture(buf, Inches(1), Inches(3), width=Inches(3))
            prs.save(str(file_path))

            with patch(
                "open_notebook.utils.document_ocr.ocr_image_bytes",
                AsyncMock(return_value="Sơ đồ Microservices API"),
            ):
                content = await extract_pptx_with_images(str(file_path))
                self.assertIn("Slide 1: Tổng quan kiến trúc", content)
                self.assertIn("Nội dung thuyết trình slide", content)
                self.assertIn(
                    "Hình ảnh tại: Slide 1: Tổng quan kiến trúc", content
                )
                self.assertIn("Sơ đồ Microservices API", content)

    async def test_extract_xlsx_with_images(self):
        import openpyxl
        from openpyxl.drawing.image import Image as XLImage

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test.xlsx"
            img_path = Path(tmp_dir) / "cell_img.png"

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Danh_Sach"
            ws["A1"] = "ID"
            ws["B1"] = "Tên"
            ws["A2"] = "1"
            ws["B2"] = "Antigravity"

            img = Image.new("RGB", (120, 60), color=(0, 0, 255))
            img.save(str(img_path))
            xl_img = XLImage(str(img_path))
            ws.add_image(xl_img, "D2")
            wb.save(str(file_path))

            with patch(
                "open_notebook.utils.document_ocr.ocr_image_bytes",
                AsyncMock(return_value="Bảng phụ lục doanh thu"),
            ):
                content = await extract_xlsx_with_images(str(file_path))
                self.assertIn("# Sheet: Danh_Sach", content)
                self.assertIn("Antigravity", content)
                self.assertIn("Sheet 'Danh_Sach', khu vực ô D2", content)
                self.assertIn("Bảng phụ lục doanh thu", content)

    async def test_extract_pdf_with_images(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test.pdf"

            # Create test PDF using PIL image with text
            img = Image.new("RGB", (300, 150), color=(255, 255, 255))
            d = ImageDraw.Draw(img)
            d.text((10, 10), "Sample PDF text", fill=(0, 0, 0))
            img.save(str(file_path))

            with patch(
                "open_notebook.utils.document_ocr.ocr_image_bytes",
                AsyncMock(return_value="PDF OCR Extracted Data"),
            ):
                content = await extract_pdf_with_images(str(file_path))
                self.assertIn("Trang 1", content)
                self.assertIn("Hình ảnh tại: Trang 1", content)
                self.assertIn("PDF OCR Extracted Data", content)

    async def test_extract_pptx_with_group_shapes(self):
        from unittest.mock import MagicMock
        from open_notebook.utils.document_ocr import _process_pptx_shape

        content_parts = []
        # Group shape mock containing a text subshape and an image subshape
        group_shape = MagicMock()
        subshape1 = MagicMock()
        subshape1.top = 100
        subshape1.left = 50
        subshape1.has_text_frame = True
        subshape1.text_frame.text = "Quy trình xử lý API mở van"
        del subshape1.shapes  # not a group
        del subshape1.image   # not an image
        subshape1.has_table = False

        subshape2 = MagicMock()
        subshape2.top = 200
        subshape2.left = 50
        del subshape2.shapes
        subshape2.has_text_frame = False
        subshape2.has_table = False
        subshape2.shape_type = 13
        subshape2.image.blob = b"fake_png"
        subshape2.image.content_type = "image/png"

        group_shape.shapes = [subshape1, subshape2]

        with patch(
            "open_notebook.utils.document_ocr.ocr_image_bytes",
            AsyncMock(return_value="Sơ đồ khối vi xử lý"),
        ):
            await _process_pptx_shape(group_shape, "Slide 1: Kiến trúc", content_parts)

        text_result = "\n".join(content_parts)
        self.assertIn("Quy trình xử lý API mở van", text_result)
        self.assertIn("Sơ đồ khối vi xử lý", text_result)

    def test_check_encrypted_office_file(self):
        from open_notebook.utils.document_ocr import (
            OLE2_HEADER,
            check_encrypted_office_file,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            enc_file = Path(tmp_dir) / "locked.xlsx"
            with open(enc_file, "wb") as f:
                f.write(
                    OLE2_HEADER
                    + b"\x00" * 100
                    + b"EncryptedPackage"
                    + b"\x00" * 100
                )

            with self.assertRaises(ValueError) as ctx:
                check_encrypted_office_file(str(enc_file))
            self.assertIn("bảo vệ bằng mật khẩu", str(ctx.exception))

            # Non-encrypted file should pass without raising
            normal_file = Path(tmp_dir) / "normal.xlsx"
            with open(normal_file, "wb") as f:
                f.write(b"PK\x03\x04" + b"\x00" * 50)
            check_encrypted_office_file(str(normal_file))

    async def test_extract_xlsx_with_drawings_and_shapes(self):
        import zipfile
        from open_notebook.utils.document_ocr import _extract_xlsx_drawings

        with tempfile.TemporaryDirectory() as tmp_dir:
            xlsx_path = Path(tmp_dir) / "drawings_test.xlsx"
            with zipfile.ZipFile(xlsx_path, "w") as z:
                # Workbook structure
                z.writestr(
                    "xl/workbook.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="ArchSheet" sheetId="1" r:id="rId1"/></sheets>'
                    '</workbook>',
                )
                z.writestr(
                    "xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                    '</Relationships>',
                )
                z.writestr(
                    "xl/worksheets/sheet1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    '</worksheet>',
                )
                z.writestr(
                    "xl/worksheets/_rels/sheet1.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rIdDrawing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
                    '</Relationships>',
                )
                z.writestr(
                    "xl/drawings/drawing1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    '<xdr:twoCellAnchor>'
                    '<xdr:from><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>10</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                    '<xdr:to><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>15</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                    '<xdr:sp>'
                    '<xdr:txBody>'
                    '<a:p><a:r><a:t>Sơ đồ kiến trúc Gateway</a:t></a:r></a:p>'
                    '<a:p><a:r><a:t>Chi tiết bộ đệm Redis</a:t></a:r></a:p>'
                    '</xdr:txBody>'
                    '</xdr:sp>'
                    '</xdr:twoCellAnchor>'
                    '</xdr:wsDr>',
                )

            res = _extract_xlsx_drawings(str(xlsx_path))
            self.assertIn("ArchSheet", res)
            items = res["ArchSheet"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["type"], "shape")
            self.assertIn("khu vực ô C11", items[0]["loc_desc"])
            self.assertIn("Sơ đồ kiến trúc Gateway", items[0]["text"])
            self.assertIn("Chi tiết bộ đệm Redis", items[0]["text"])


    async def test_extract_xlsx_flowchart_connectors(self):
        import zipfile
        from open_notebook.utils.document_ocr import _extract_xlsx_drawings

        with tempfile.TemporaryDirectory() as tmp_dir:
            xlsx_path = Path(tmp_dir) / "flowchart_test.xlsx"
            with zipfile.ZipFile(xlsx_path, "w") as z:
                z.writestr(
                    "xl/workbook.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="FlowSheet" sheetId="1" r:id="rId1"/></sheets>'
                    '</workbook>',
                )
                z.writestr(
                    "xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                    '</Relationships>',
                )
                z.writestr(
                    "xl/worksheets/sheet1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    '</worksheet>',
                )
                z.writestr(
                    "xl/worksheets/_rels/sheet1.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rIdDrawing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
                    '</Relationships>',
                )
                z.writestr(
                    "xl/drawings/drawing1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    '<xdr:twoCellAnchor>'
                    '<xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                    '<xdr:to><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>4</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                    '<xdr:sp><xdr:nvSpPr><xdr:cNvPr id="10" name="Box 1"/></xdr:nvSpPr>'
                    '<xdr:txBody><a:p><a:r><a:t>Bước A: Nhận Request</a:t></a:r></a:p></xdr:txBody>'
                    '</xdr:sp>'
                    '</xdr:twoCellAnchor>'
                    '<xdr:twoCellAnchor>'
                    '<xdr:from><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                    '<xdr:to><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>4</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                    '<xdr:sp><xdr:nvSpPr><xdr:cNvPr id="20" name="Box 2"/></xdr:nvSpPr>'
                    '<xdr:txBody><a:p><a:r><a:t>Bước B: Xử lý dữ liệu</a:t></a:r></a:p></xdr:txBody>'
                    '</xdr:sp>'
                    '</xdr:twoCellAnchor>'
                    '<xdr:twoCellAnchor>'
                    '<xdr:from><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>3</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                    '<xdr:to><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>3</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                    '<xdr:cxnSp><xdr:nvCxnSpPr><xdr:cNvPr id="30" name="Mũi tên liên kết 1"/>'
                    '<xdr:cNvCxnSpPr><a:stCxn id="10" idx="3"/><a:endCxn id="20" idx="1"/></xdr:cNvCxnSpPr>'
                    '</xdr:nvCxnSpPr>'
                    '<xdr:spPr><a:ln><a:tailEnd type="triangle"/></a:ln></xdr:spPr>'
                    '</xdr:cxnSp>'
                    '</xdr:twoCellAnchor>'
                    '</xdr:wsDr>',
                )

            res = _extract_xlsx_drawings(str(xlsx_path))
            self.assertIn("FlowSheet", res)
            flows = [it["text"] for it in res["FlowSheet"] if it["type"] == "flow"]
            self.assertEqual(len(flows), 1)
            self.assertIn('"Bước A: Nhận Request" -> "Bước B: Xử lý dữ liệu"', flows[0])
            self.assertIn('via connector "Mũi tên liên kết 1"', flows[0])

    async def test_extract_pptx_flowchart_connectors(self):
        from pptx import Presentation
        from pptx.util import Inches
        from pptx.enum.shapes import MSO_SHAPE_TYPE, MSO_CONNECTOR
        from open_notebook.utils.document_ocr import extract_pptx_with_images

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "flow_test.pptx"
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[6])

            sp1 = slide.shapes.add_shape(MSO_SHAPE_TYPE.AUTO_SHAPE, Inches(1), Inches(1), Inches(2), Inches(1))
            sp1.text = "Khởi tạo API"

            sp2 = slide.shapes.add_shape(MSO_SHAPE_TYPE.AUTO_SHAPE, Inches(4), Inches(1), Inches(2), Inches(1))
            sp2.text = "Xác thực Token"

            connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(3), Inches(1.5), Inches(4), Inches(1.5))
            connector.begin_connect(sp1, 3)
            connector.end_connect(sp2, 1)

            prs.save(str(file_path))

            content = await extract_pptx_with_images(str(file_path))
            self.assertIn("Luồng quy trình & Lưu đồ (Flowchart & Connections)", content)
            self.assertIn('"Khởi tạo API" -> "Xác thực Token"', content)
            self.assertIn(f'via connector "{connector.name}"', content)

    def test_classify_color_hex(self):
        self.assertEqual(classify_color_hex("FF92D050", is_font=False), "[Màu xanh lá]")
        self.assertEqual(classify_color_hex("C6EFCE", is_font=False), "[Màu xanh lá]")
        self.assertEqual(classify_color_hex("FFFF00", is_font=False), "[Màu vàng]")
        self.assertEqual(classify_color_hex("FFEB9C", is_font=False), "[Màu vàng]")
        self.assertEqual(classify_color_hex("FFFF0000", is_font=True), "[Màu đỏ]")
        self.assertEqual(classify_color_hex("FFC7CE", is_font=False), "[Màu đỏ]")
        # Ignored header blues and grays
        self.assertIsNone(classify_color_hex("FF99CCFF", is_font=False))
        self.assertIsNone(classify_color_hex("FFBDD7EE", is_font=False))
        self.assertIsNone(classify_color_hex("F2F2F2", is_font=False))
        self.assertIsNone(classify_color_hex("FFFFFF", is_font=False))
        self.assertIsNone(classify_color_hex("000000", is_font=False))

    async def test_extract_xlsx_strikethrough_colors_and_comments(self):
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        from openpyxl.comments import Comment

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test_formatting.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Specifications"

            # 1. Normal cell
            ws["A1"] = "Mục"
            ws["B1"] = "Chi tiết"

            # 2. Strikethrough cell
            c2 = ws["A2"]
            c2.value = "HULFT Send"
            c2.font = Font(strike=True)

            # 3. Green fill cell
            c3 = ws["B2"]
            c3.value = "Azure Functions"
            c3.fill = PatternFill(
                start_color="FF92D050", end_color="FF92D050", fill_type="solid"
            )

            # 4. Red font cell
            c4 = ws["A3"]
            c4.value = "Backup Blob Path"
            c4.font = Font(color="FFFF0000")

            # 5. Comment
            ws["B3"] = "Cần thẩm định"
            ws["B3"].comment = Comment("Kiểm tra với đội hạ tầng", "Reviewer 1")

            wb.save(str(file_path))

            content = await extract_xlsx_with_images(str(file_path))
            self.assertIn("~~HULFT Send~~", content)
            self.assertIn("[Màu xanh lá] Azure Functions", content)
            self.assertIn("[Màu đỏ] Backup Blob Path", content)
            self.assertIn("Ghi chú ô tính (Cell Comments)", content)
            self.assertIn("Kiểm tra với đội hạ tầng", content)

    async def test_extract_docx_strikethrough_colors_and_comments(self):
        import docx
        from docx.enum.text import WD_COLOR_INDEX
        from docx.shared import RGBColor
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test_formatting.docx"
            doc = docx.Document()

            p = doc.add_paragraph("Bắt đầu ")
            r_strike = p.add_run("Nội dung cũ bị xoá")
            r_strike.font.strike = True

            r_hl = p.add_run(" và ")
            r_hl2 = p.add_run("Nội dung được highlight vàng")
            r_hl2.font.highlight_color = WD_COLOR_INDEX.YELLOW

            r_red = p.add_run(" và ")
            r_red2 = p.add_run("Nội dung chỉnh sửa chữ đỏ")
            r_red2.font.color.rgb = RGBColor(255, 0, 0)

            # Table with green shading
            tbl = doc.add_table(rows=1, cols=1)
            cell = tbl.cell(0, 0)
            cell.text = "Tính năng mới bảng"
            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="92D050"/>')
            cell._tc.get_or_add_tcPr().append(shd)

            doc.save(str(file_path))

            content = await extract_docx_with_images(str(file_path))
            self.assertIn("~~Nội dung cũ bị xoá~~", content)
            self.assertIn("[Màu vàng] Nội dung được highlight vàng", content)
            self.assertIn("[Màu đỏ] Nội dung chỉnh sửa chữ đỏ", content)
            self.assertIn("[Màu xanh lá] Tính năng mới bảng", content)

    async def test_extract_pptx_strikethrough_colors_and_notes(self):
        from pptx import Presentation
        from pptx.dml.color import RGBColor

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test_formatting.pptx"
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[6])

            tb = slide.shapes.add_textbox(0, 0, 300, 200)
            p = tb.text_frame.paragraphs[0]
            r1 = p.add_run()
            r1.text = "Chức năng cũ"
            r1._r.get_or_add_rPr().set("strike", "sngStrike")

            r2 = p.add_run()
            r2.text = " Chức năng chữ đỏ"
            r2.font.color.rgb = RGBColor(255, 0, 0)

            # Speaker notes
            slide.notes_slide.notes_text_frame.text = "Nhớ nhấn mạnh tiến độ quý 4"

            prs.save(str(file_path))

            content = await extract_pptx_with_images(str(file_path))
            self.assertIn("~~Chức năng cũ~~", content)
            self.assertIn("[Màu đỏ] Chức năng chữ đỏ", content)
            self.assertIn("Ghi chú diễn giả (Speaker Notes)", content)
            self.assertIn("Nhớ nhấn mạnh tiến độ quý 4", content)

    async def test_extract_pdf_annotations(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "test_annot.pdf"

            class MockPage:
                annots = [
                    {
                        "data": {"Subtype": "/StrikeOut"},
                        "x0": 10,
                        "top": 10,
                        "x1": 50,
                        "bottom": 20,
                        "contents": "Xóa khoản 3 điều 5",
                        "title": "Reviewer A",
                    },
                    {
                        "data": {"Subtype": "/Text"},
                        "contents": "Bổ sung tài liệu tham khảo",
                        "title": "Trưởng phòng",
                    },
                ]
                images = []

                def extract_text(self):
                    return "Văn bản mẫu điều khoản hợp đồng"

                def extract_tables(self):
                    return []

                def crop(self, bbox):
                    class MockCrop:
                        def extract_text(self):
                            return "Khoản 3 điều 5"

                    return MockCrop()

            class MockPDF:
                pages = [MockPage()]

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            with patch("pdfplumber.open", return_value=MockPDF()):
                content = await extract_pdf_with_images(str(file_path))
                self.assertIn("Văn bản mẫu", content)
                self.assertIn("Ghi chú & Nhận xét trên trang (Page Annotations)", content)
                self.assertIn("~~Khoản 3 điều 5~~", content)
                self.assertIn("Bổ sung tài liệu tham khảo", content)


if __name__ == "__main__":
    unittest.main()


