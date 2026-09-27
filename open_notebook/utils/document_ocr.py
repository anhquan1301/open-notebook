"""
Document extraction service with embedded image OCR and contextual positioning.

Supports:
- Word (.docx): Preserves headings, paragraphs, tables, and extracts embedded drawings/images with heading context.
- PowerPoint (.pptx): Preserves slides, titles, shapes, tables, and extracts embedded pictures with slide context.
- Excel (.xlsx): Preserves sheets and tables, extracts sheet images with cell anchor coordinates.
- PDF (.pdf): Preserves pages, text, tables, and extracts embedded images with page context.

All extracted images are OCR-ed using the Hybrid OCR engine (Vision LLM -> RapidOCR fallback)
and formatted with clear markdown blockquotes indicating their exact contextual location.
"""

import asyncio
import hashlib
import io
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET
import zipfile

from loguru import logger
from PIL import Image

from open_notebook.utils.ocr_service import ocr_image_bytes
from open_notebook.utils.text_utils import clean_markdown_content


OLE2_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def check_encrypted_office_file(file_path: str) -> None:
    """
    Check if a modern Office file (.docx, .xlsx, .pptx) is password-protected/encrypted.
    Modern OpenXML files are ZIP archives starting with PK (b'PK\x03\x04').
    When password-protected by Office, they are wrapped in an OLE2 Compound File Binary
    container starting with b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in {".xlsx", ".docx", ".pptx"}:
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)
                if header == OLE2_HEADER:
                    f.seek(0)
                    chunk = f.read(4096)
                    if (
                        b"EncryptedPackage" in chunk
                        or b"EncryptionInfo" in chunk
                        or b"DataSpaces" in chunk
                    ):
                        raise ValueError(
                            f"Tệp tin '{os.path.basename(file_path)}' đang được bảo vệ bằng mật khẩu (Password-Protected / Encrypted). "
                            "Hệ thống không thể giải mã nội dung của tệp có mật khẩu. "
                            "Vui lòng mở tệp trong Office, gỡ mật khẩu bảo vệ và tải lên lại."
                        )
        except ValueError:
            raise
        except Exception as e:
            logger.debug(f"Could not check encryption header for {file_path}: {e}")



SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".docx",
    ".pptx",
    ".xlsx",
    ".pdf",
}

SUPPORTED_DOCUMENT_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/pdf",
}


def is_document_with_images_supported(
    file_path: str, mime: Optional[str] = None
) -> bool:
    """Check if the file is a document format supported for embedded image OCR."""
    if mime and mime.lower() in SUPPORTED_DOCUMENT_MIMES:
        return True
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_DOCUMENT_EXTENSIONS


def format_image_ocr_callout(location_label: str, ocr_text: str) -> str:
    """
    Format OCR text as a structured Markdown blockquote with clear contextual anchor.
    """
    lines = ocr_text.strip().splitlines()
    quoted_lines = [f"> {line}" if line.strip() else ">" for line in lines]
    quoted_content = "\n".join(quoted_lines)
    return (
        f"\n\n> 🖼️ **[Hình ảnh tại: {location_label}]**\n"
        f"> *(Nội dung trích xuất từ hình ảnh)*:\n"
        f"{quoted_content}\n\n"
    )


def classify_color_hex(hex_str: Optional[str], is_font: bool = False) -> Optional[str]:
    """
    Classify a hex color (RRGGBB or AARRGGBB) into pure color name tags.
    - Yellow / Amber -> [Màu vàng]
    - Orange -> [Màu cam]
    - Green -> [Màu xanh lá]
    - Red / Pink -> [Màu đỏ]
    - Header blues, cyans, light grays, pure white, pure black -> None (ignored to avoid noise)
    """
    if not hex_str:
        return None
    hex_clean = hex_str.strip().lstrip("#")
    # If 8 characters (ARGB format), strip alpha channel
    if len(hex_clean) == 8:
        hex_clean = hex_clean[2:]
    if len(hex_clean) != 6:
        return None
    try:
        r = int(hex_clean[0:2], 16)
        g = int(hex_clean[2:4], 16)
        b = int(hex_clean[4:6], 16)
    except ValueError:
        return None

    # Ignore pure black, near black (<40)
    if max(r, g, b) < 40:
        return None
    # Ignore pure white or near white (>235 with minimal delta)
    if min(r, g, b) > 235 and (max(r, g, b) - min(r, g, b)) < 25:
        return None
    # Ignore grayscales (where R, G, B are practically equal)
    if (max(r, g, b) - min(r, g, b)) < 20:
        return None

    # Ignore blues / cyans (typical header styling in Japanese & corporate specs)
    if b > r + 30 and b > g - 15:
        return None

    # 1. Yellow / Amber (high red and green, low blue)
    if r > 180 and g > 140 and b < min(r, g) * 0.82:
        return "[Màu vàng]"

    # 1b. Orange (high red, moderate green, low blue)
    if r > 190 and 80 < g <= 140 and b < 90:
        return "[Màu cam]"

    # 2. Green (green clearly dominates red and blue)
    if g > 115 and g > r * 1.05 and g > b * 1.05:
        return "[Màu xanh lá]"

    # 3. Red / Pink (red clearly dominates green and blue)
    if r > 160 and r > g * 1.15 and r > b * 1.15:
        return "[Màu đỏ]"

    return None


def extract_openpyxl_color_hex(color_obj) -> Optional[str]:
    """Safely extract hex RGB string from openpyxl Color object without raising errors."""
    if not color_obj:
        return None
    try:
        c_type = getattr(color_obj, "type", None)
        if c_type == "rgb":
            rgb_val = getattr(color_obj, "rgb", None)
            if isinstance(rgb_val, str) and rgb_val:
                return rgb_val
    except Exception:
        pass
    return None


def _extract_docx_comments(file_path: str) -> List[str]:
    """Extract review comments from word/comments.xml if present in docx archive."""
    comments = []
    try:
        with zipfile.ZipFile(file_path, "r") as z:
            if "word/comments.xml" in z.namelist():
                root = ET.fromstring(z.read("word/comments.xml"))
                for c in root.iter():
                    if c.tag.endswith("}comment"):
                        author = c.attrib.get(
                            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}author",
                            c.attrib.get("author", "Người nhận xét"),
                        )
                        t_parts = [
                            t.text for t in c.iter() if t.tag.endswith("}t") and t.text
                        ]
                        comment_text = "".join(t_parts).strip()
                        if comment_text:
                            comments.append(f"> - **[{author}]**: {comment_text}")
    except Exception as e:
        logger.debug(f"Could not extract Word comments from {file_path}: {e}")
    return comments


def _format_docx_paragraph(p, doc) -> str:
    """Format a docx Paragraph element preserving strikethrough, colors, and track changes."""
    parts: List[str] = []
    for child in p._element:
        tag = child.tag.split("}")[-1]
        if tag == "r":
            from docx.text.run import Run

            run = Run(child, p)
            txt = run.text
            if not txt:
                continue
            is_strike = bool(
                run.font
                and (
                    run.font.strike
                    or getattr(run.font, "double_strike", False)
                )
            )
            if is_strike:
                parts.append(f"~~{txt}~~")
            else:
                color_tag = None
                if run.font and run.font.highlight_color:
                    hl_name = str(run.font.highlight_color).upper()
                    if "YELLOW" in hl_name:
                        color_tag = "[Màu vàng]"
                    elif "GREEN" in hl_name:
                        color_tag = "[Màu xanh lá]"
                    elif "RED" in hl_name or "PINK" in hl_name:
                        color_tag = "[Màu đỏ]"
                    elif "CYAN" in hl_name or "BLUE" in hl_name:
                        color_tag = "[Màu xanh dương]"
                if (
                    not color_tag
                    and run.font
                    and run.font.color
                    and getattr(run.font.color, "rgb", None)
                ):
                    try:
                        color_tag = classify_color_hex(
                            str(run.font.color.rgb), is_font=True
                        )
                    except Exception:
                        pass

                if run.bold and run.italic:
                    txt = f"***{txt}***"
                elif run.bold:
                    txt = f"**{txt}**"
                elif run.italic:
                    txt = f"*{txt}*"

                if color_tag:
                    if txt.startswith(" "):
                        txt = f" {color_tag} {txt.lstrip()}"
                    else:
                        txt = f"{color_tag} {txt}"
                parts.append(txt)

        elif tag == "del":
            # Track Changes deletion
            del_texts = child.xpath('.//*[local-name()="delText"]/text()')
            del_txt = "".join(del_texts).strip()
            if del_txt:
                parts.append(f"~~{del_txt}~~")

        elif tag == "ins":
            # Track Changes insertion
            ins_texts = child.xpath('.//*[local-name()="t"]/text()')
            ins_txt = "".join(ins_texts).strip()
            if ins_txt:
                parts.append(ins_txt)

        elif tag == "hyperlink":
            # Hyperlink containing runs
            for sub_r in child.xpath('.//*[local-name()="r"]'):
                from docx.text.run import Run

                sub_run = Run(sub_r, p)
                if sub_run.text:
                    if sub_run.font and (
                        sub_run.font.strike
                        or getattr(sub_run.font, "double_strike", False)
                    ):
                        parts.append(f"~~{sub_run.text}~~")
                    else:
                        parts.append(sub_run.text)

        elif tag == "sdt":
            # Structured document tag (checkbox, dropdown, etc.)
            checked = child.xpath(
                './/*[local-name()="checked"]/@*[local-name()="val"]'
            )
            if checked:
                is_checked = checked[0] in ("1", "true")
                parts.append("[x] " if is_checked else "[ ] ")
            sdt_text = "".join(child.xpath('.//*[local-name()="t"]/text()')).strip()
            if sdt_text:
                parts.append(sdt_text)

    res = "".join(parts).strip()
    return res if res else p.text.strip()


# ---------------------------------------------------------------------------
# Word (.docx) Extraction
# ---------------------------------------------------------------------------

async def extract_docx_with_images(file_path: str) -> str:
    """
    Extract content from DOCX file, preserving document order (paragraphs and tables)
    with strikethrough, highlights, font colors, track changes, and comments,
    and running OCR on embedded images with heading context.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def _load_doc():
        return Document(file_path)

    doc = await asyncio.to_thread(_load_doc)
    content_parts: List[str] = []
    seen_image_hashes: Set[str] = set()
    current_heading = "Phần mở đầu"

    for child in doc.element.body:
        tag = child.tag.split("}")[-1]

        if tag == "p":
            p = Paragraph(child, doc)
            text = p.text.strip()
            style_name = p.style.name if p.style else ""

            # Check if paragraph is a heading
            if "Heading" in style_name:
                level = 1
                for char in style_name:
                    if char.isdigit():
                        level = int(char)
                        break
                current_heading = text if text else current_heading
                heading_prefix = "#" * min(level, 6)
                content_parts.append(f"\n{heading_prefix} {text}\n")
            elif text:
                para_content = _format_docx_paragraph(p, doc)
                if para_content:
                    content_parts.append(para_content)

            # Check for embedded drawings / images inside this paragraph
            r_ids = child.xpath(
                './/*[local-name()="blip"]/@*[local-name()="embed"] | .//*[local-name()="imagedata"]/@*[local-name()="id"]'
            )
            for r_id in r_ids:
                part = doc.part.related_parts.get(r_id)
                if part and getattr(part, "content_type", "").startswith("image/"):
                    try:
                        image_bytes = part.blob
                        img_hash = hashlib.sha256(image_bytes).hexdigest()
                        if img_hash in seen_image_hashes:
                            continue
                        seen_image_hashes.add(img_hash)
                        mime = part.content_type
                        ocr_result = await ocr_image_bytes(image_bytes, mime)
                        if ocr_result:
                            callout = format_image_ocr_callout(
                                current_heading, ocr_result
                            )
                            content_parts.append(callout)
                    except Exception as e:
                        logger.warning(
                            f"Failed to OCR image in DOCX paragraph: {e}"
                        )

        elif tag == "tbl":
            tbl = Table(child, doc)
            table_rows: List[List[str]] = []
            for row in tbl.rows:
                row_cells: List[str] = []
                for cell in row.cells:
                    # Check cell background shading
                    shd_list = cell._element.xpath(
                        './/*[local-name()="shd"]/@*[local-name()="fill"]'
                    )
                    cell_tag = None
                    if shd_list and shd_list[0] and shd_list[0].lower() != "auto":
                        cell_tag = classify_color_hex(shd_list[0], is_font=False)

                    # Also check for images inside table cells
                    cell_images = cell._element.xpath(
                        './/*[local-name()="blip"]/@*[local-name()="embed"] | .//*[local-name()="imagedata"]/@*[local-name()="id"]'
                    )

                    cell_p_texts = []
                    for cp in cell.paragraphs:
                        fmt_cp = _format_docx_paragraph(cp, doc)
                        if fmt_cp:
                            cell_p_texts.append(fmt_cp)
                    cell_text = " ".join(cell_p_texts).replace("\n", " ").strip()
                    if not cell_text:
                        cell_text = cell.text.strip().replace("\n", " ")
                    if cell_tag and not cell_text.startswith(cell_tag):
                        cell_text = f"{cell_tag} {cell_text}"

                    for r_id in cell_images:
                        part = doc.part.related_parts.get(r_id)
                        if part and getattr(part, "content_type", "").startswith("image/"):
                            try:
                                img_bytes = part.blob
                                img_hash = hashlib.sha256(img_bytes).hexdigest()
                                if img_hash in seen_image_hashes:
                                    continue
                                seen_image_hashes.add(img_hash)
                                ocr_res = await ocr_image_bytes(
                                    img_bytes, part.content_type
                                )
                                if ocr_res:
                                    cell_text += f" [Ảnh: {ocr_res[:50]}...]"
                            except Exception:
                                pass
                    row_cells.append(cell_text)
                table_rows.append(row_cells)

            if table_rows:
                # Render markdown table
                num_cols = max(len(r) for r in table_rows)
                # Pad rows to same column count
                padded = [r + [""] * (num_cols - len(r)) for r in table_rows]
                header = padded[0]
                md_table = [
                    "| " + " | ".join(header) + " |",
                    "| " + " | ".join(["---"] * num_cols) + " |",
                ]
                for r in padded[1:]:
                    md_table.append("| " + " | ".join(r) + " |")
                content_parts.append("\n" + "\n".join(md_table) + "\n")

    # Word comments
    comments = await asyncio.to_thread(_extract_docx_comments, file_path)
    if comments:
        content_parts.append(
            "\n### 💬 Nhận xét trong tài liệu (Document Comments):\n"
            + "\n".join(comments)
            + "\n"
        )

    return "\n\n".join(content_parts)


# ---------------------------------------------------------------------------
# PowerPoint (.pptx) Extraction
def _safe_shape_coord(shape) -> Tuple[int, int]:
    try:
        top = getattr(shape, "top", 0)
        left = getattr(shape, "left", 0)
        t = int(top) if isinstance(top, (int, float)) else 0
        l = int(left) if isinstance(left, (int, float)) else 0
        return (t, l)
    except Exception:
        return (0, 0)


async def _process_pptx_shape(
    shape,
    loc_label: str,
    content_parts: List[str],
    seen_image_hashes: Optional[Set[str]] = None,
) -> None:
    """
    Recursively extract content from a PPTX shape, handling GroupShapes,
    SmartArt, tables, images (with OCR), and text frames.
    """
    # 1. Group shape -> recursively process children in natural reading order
    if hasattr(shape, "shapes"):
        try:
            sorted_subshapes = sorted(
                shape.shapes,
                key=_safe_shape_coord,
            )
            for subshape in sorted_subshapes:
                await _process_pptx_shape(
                    subshape, loc_label, content_parts, seen_image_hashes
                )
            return
        except Exception as e:
            logger.warning(f"Failed to process grouped shapes in {loc_label}: {e}")

    # 2. Picture
    if getattr(shape, "shape_type", None) == 13 or hasattr(shape, "image"):
        try:
            img_bytes = shape.image.blob
            if seen_image_hashes is not None:
                img_hash = hashlib.sha256(img_bytes).hexdigest()
                if img_hash in seen_image_hashes:
                    return
                seen_image_hashes.add(img_hash)
            mime = getattr(shape.image, "content_type", "image/png")
            ocr_res = await ocr_image_bytes(img_bytes, mime)
            if ocr_res:
                content_parts.append(
                    format_image_ocr_callout(loc_label, ocr_res)
                )
        except Exception as e:
            logger.warning(
                f"Failed to extract image from PPTX shape in {loc_label}: {e}"
            )
        return

    # 3. Table
    if getattr(shape, "has_table", False):
        try:
            table = shape.table
            table_rows = []
            for row in table.rows:
                row_cells = []
                for cell in row.cells:
                    cell_fill_tag = None
                    try:
                        fill_xml = cell._tc.xpath(
                            './/*[local-name()="solidFill"]/*[local-name()="srgbClr"]/@val'
                        )
                        if fill_xml:
                            cell_fill_tag = classify_color_hex(fill_xml[0], is_font=False)
                    except Exception:
                        pass

                    cell_p_texts = []
                    if hasattr(cell, "text_frame") and cell.text_frame:
                        for p in cell.text_frame.paragraphs:
                            p_parts = []
                            for r in p.runs:
                                r_txt = r.text
                                if not r_txt:
                                    continue
                                rPr = getattr(r._r, "rPr", None)
                                strike = rPr.get("strike") if rPr is not None else None
                                if strike in ("sngStrike", "dblStrike"):
                                    p_parts.append(f"~~{r_txt}~~")
                                else:
                                    r_color_tag = None
                                    if (
                                        r.font
                                        and r.font.color
                                        and getattr(r.font.color, "type", None) is not None
                                    ):
                                        try:
                                            r_color_tag = classify_color_hex(
                                                str(r.font.color.rgb), is_font=True
                                            )
                                        except Exception:
                                            pass
                                    if r_color_tag:
                                        p_parts.append(f"{r_color_tag} {r_txt}")
                                    else:
                                        p_parts.append(r_txt)
                            if p_parts:
                                cell_p_texts.append("".join(p_parts))

                    cell_full = " ".join(cell_p_texts).strip().replace("\n", " ")
                    if not cell_full:
                        cell_full = cell.text.strip().replace("\n", " ")
                    if cell_fill_tag and not cell_full.startswith(cell_fill_tag):
                        cell_full = f"{cell_fill_tag} {cell_full}"
                    row_cells.append(cell_full)
                table_rows.append(row_cells)

            if table_rows:
                num_cols = max(len(r) for r in table_rows)
                padded = [r + [""] * (num_cols - len(r)) for r in table_rows]
                md_table = [
                    "| " + " | ".join(padded[0]) + " |",
                    "| " + " | ".join(["---"] * num_cols) + " |",
                ]
                for r in padded[1:]:
                    md_table.append("| " + " | ".join(r) + " |")
                content_parts.append("\n" + "\n".join(md_table) + "\n")
        except Exception as e:
            logger.warning(f"Failed to extract table in {loc_label}: {e}")
        return

    # 4. Text frame / AutoShape / Text box
    if getattr(shape, "has_text_frame", False):
        try:
            shape_fill_tag = None
            try:
                sp_xml = shape.element.xpath(
                    './/*[local-name()="spPr"]/*[local-name()="solidFill"]/*[local-name()="srgbClr"]/@val'
                )
                if sp_xml:
                    shape_fill_tag = classify_color_hex(sp_xml[0], is_font=False)
            except Exception:
                pass

            para_lines = []
            if hasattr(shape.text_frame, "paragraphs"):
                for p in shape.text_frame.paragraphs:
                    p_parts = []
                    runs = getattr(p, "runs", [])
                    if isinstance(runs, (list, tuple)):
                        for r in runs:
                            r_txt = getattr(r, "text", "")
                            if not isinstance(r_txt, str) or not r_txt:
                                continue
                            rPr = getattr(r, "_r", None)
                            rPr_el = getattr(rPr, "rPr", None) if rPr is not None else None
                            strike = rPr_el.get("strike") if rPr_el is not None else None
                            if strike in ("sngStrike", "dblStrike"):
                                p_parts.append(f"~~{r_txt}~~")
                            else:
                                r_color_tag = None
                                if (
                                    hasattr(r, "font")
                                    and r.font
                                    and getattr(r.font, "color", None)
                                    and getattr(r.font.color, "type", None) is not None
                                ):
                                    try:
                                        r_color_tag = classify_color_hex(
                                            str(r.font.color.rgb), is_font=True
                                        )
                                    except Exception:
                                        pass
                                if r_color_tag:
                                    if r_txt.startswith(" "):
                                        p_parts.append(f" {r_color_tag} {r_txt.lstrip()}")
                                    else:
                                        p_parts.append(f"{r_color_tag} {r_txt}")
                                else:
                                    p_parts.append(r_txt)
                    if p_parts:
                        p_line = "".join(str(x) for x in p_parts).strip()
                        if p_line:
                            para_lines.append(p_line)

            if para_lines:
                full_text = "\n".join(para_lines)
                if shape_fill_tag:
                    full_text = f"{shape_fill_tag} {full_text}"
                content_parts.append(full_text)
                return
            else:
                raw_tf_text = getattr(shape.text_frame, "text", "")
                if isinstance(raw_tf_text, str) and raw_tf_text.strip():
                    txt = raw_tf_text.strip()
                    if shape_fill_tag:
                        txt = f"{shape_fill_tag} {txt}"
                    content_parts.append(txt)
                    return
        except Exception:
            pass

    # 5. Direct text attribute fallback
    if hasattr(shape, "text"):
        try:
            raw_text = shape.text
            if isinstance(raw_text, str) and raw_text.strip():
                content_parts.append(raw_text.strip())
        except Exception:
            pass


def _clean_flow_label(text: str, name: str, max_len: int = 60) -> str:
    """Format shape text or shape name into a concise node label for flowcharts."""
    if text and text.strip():
        cleaned = " ".join(text.strip().splitlines())
        if len(cleaned) > max_len:
            cleaned = cleaned[: max_len - 3] + "..."
        return f'"{cleaned}"'
    return f"[{name}]" if name else "[Shape]"


def _point_to_box_dist(
    px: float, py: float, bx1: float, by1: float, bx2: float, by2: float
) -> float:
    """Calculate Euclidean distance from a 2D point to a bounding box."""
    dx = max(0.0, bx1 - px, px - bx2)
    dy = max(0.0, by1 - py, py - by2)
    return (dx**2 + dy**2) ** 0.5


def _collect_pptx_slide_shapes(
    shape,
    shapes_map: dict,
    connectors: list,
) -> None:
    """Recursively collect shapes and connectors from a PPTX slide (including GroupShapes)."""
    if hasattr(shape, "shapes"):
        try:
            for subshape in shape.shapes:
                _collect_pptx_slide_shapes(subshape, shapes_map, connectors)
            return
        except Exception:
            pass

    tag = getattr(shape, "element", None)
    tag_name = tag.tag.split("}")[-1] if tag is not None else ""
    if tag_name == "cxnSp" or getattr(shape, "shape_type", None) == 9:
        connectors.append(shape)
        return

    sid = str(getattr(shape, "shape_id", getattr(shape, "id", id(shape))))
    sname = getattr(shape, "name", "")
    text = ""
    if getattr(shape, "has_text_frame", False):
        try:
            text = shape.text_frame.text.strip()
        except Exception:
            pass
    elif hasattr(shape, "text"):
        try:
            text = shape.text.strip()
        except Exception:
            pass

    left = getattr(shape, "left", 0) or 0
    top = getattr(shape, "top", 0) or 0
    width = getattr(shape, "width", 0) or 0
    height = getattr(shape, "height", 0) or 0
    bbox = (float(left), float(top), float(left + width), float(top + height))

    shapes_map[sid] = {
        "id": sid,
        "name": sname,
        "text": text,
        "bbox": bbox,
    }


def _extract_pptx_slide_flows(slide) -> List[str]:
    """Extract flowchart connections from slide shapes and connectors."""
    shapes_map: dict = {}
    connectors: list = []
    for s in slide.shapes:
        _collect_pptx_slide_shapes(s, shapes_map, connectors)

    flows: List[str] = []
    for c in connectors:
        st_id, end_id = None, None
        head, tail = None, None
        c_name = getattr(c, "name", "Connector")

        if hasattr(c, "element"):
            for el in c.element.iter():
                if el.tag.endswith("}stCxn"):
                    st_id = el.attrib.get("id")
                elif el.tag.endswith("}endCxn"):
                    end_id = el.attrib.get("id")
                elif el.tag.endswith("}headEnd"):
                    head = el.attrib.get("type", "none")
                elif el.tag.endswith("}tailEnd"):
                    tail = el.attrib.get("type", "none")

        arrow = "->"
        if head not in (None, "none") and tail not in (None, "none"):
            arrow = "<->"
        elif head not in (None, "none"):
            arrow = "<-"

        bx = getattr(c, "begin_x", None)
        by = getattr(c, "begin_y", None)
        ex = getattr(c, "end_x", None)
        ey = getattr(c, "end_y", None)

        if not st_id and bx is not None and by is not None and shapes_map:
            closest = min(
                shapes_map.values(),
                key=lambda s: _point_to_box_dist(float(bx), float(by), *s["bbox"]),
            )
            st_id = closest["id"]

        if not end_id and ex is not None and ey is not None and shapes_map:
            closest = min(
                shapes_map.values(),
                key=lambda s: _point_to_box_dist(float(ex), float(ey), *s["bbox"]),
            )
            end_id = closest["id"]

        src = shapes_map.get(st_id)
        dst = shapes_map.get(end_id)
        if src and dst and src["id"] != dst["id"]:
            src_lbl = _clean_flow_label(src["text"], src["name"])
            dst_lbl = _clean_flow_label(dst["text"], dst["name"])
            flows.append(f'- {src_lbl} {arrow} {dst_lbl} via connector "{c_name}"')

    return flows


async def extract_pptx_with_images(file_path: str) -> str:
    """
    Extract content from PPTX file, preserving slide numbers, titles, shapes (including
    nested GroupShapes and flowcharts), and running OCR on embedded pictures with slide context.
    """
    from pptx import Presentation

    def _load_prs():
        return Presentation(file_path)

    prs = await asyncio.to_thread(_load_prs)
    content_parts: List[str] = []
    seen_image_hashes: Set[str] = set()
    seen_speaker_notes: dict[str, int] = {}

    for slide_num, slide in enumerate(prs.slides, 1):
        slide_title = ""
        if slide.shapes.title and slide.shapes.title.text.strip():
            slide_title = slide.shapes.title.text.strip()

        loc_label = f"Slide {slide_num}" + (f": {slide_title}" if slide_title else "")
        content_parts.append(f"\n# {loc_label}\n")

        # Sort shapes from top to bottom, left to right for natural reading order
        sorted_shapes = sorted(
            slide.shapes,
            key=_safe_shape_coord,
        )

        for shape in sorted_shapes:
            # Skip title if already printed
            if shape == slide.shapes.title:
                continue
            await _process_pptx_shape(
                shape, loc_label, content_parts, seen_image_hashes
            )

        # Extract flowchart connections on this slide
        slide_flows = _extract_pptx_slide_flows(slide)
        if slide_flows:
            content_parts.append("\n### 🔄 Luồng quy trình & Lưu đồ (Flowchart & Connections):\n")
            for flow_line in slide_flows:
                content_parts.append(flow_line)

        # Extract speaker notes on this slide
        if getattr(slide, "has_notes_slide", False):
            try:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    if notes_text in seen_speaker_notes:
                        orig_slide = seen_speaker_notes[notes_text]
                        content_parts.append(
                            f"\n### 🗣️ Ghi chú diễn giả (Speaker Notes):\n*(Tương tự ghi chú tại Slide {orig_slide})*\n"
                        )
                    else:
                        seen_speaker_notes[notes_text] = slide_num
                        content_parts.append(
                            f"\n### 🗣️ Ghi chú diễn giả (Speaker Notes):\n> {notes_text}\n"
                        )
            except Exception as e:
                logger.debug(f"Failed to extract speaker notes on slide {slide_num}: {e}")

    return "\n\n".join(content_parts)


# ---------------------------------------------------------------------------
# Excel (.xlsx) Extraction
# ---------------------------------------------------------------------------

def _extract_xlsx_drawings(file_path: str) -> dict[str, list[dict]]:
    """
    Extract all shapes (text boxes, flowchart nodes, callouts, group shapes)
    and embedded drawing pictures from Excel sheets via DrawingML (xl/drawings/drawing*.xml).
    Returns mapping: sheet_name -> list of items sorted by (row, col) position.
    Each item: {"type": "shape"|"image", "sort_key": tuple, "loc_desc": str, "text"?: str, "image_bytes"?: bytes}
    """
    from openpyxl.utils import get_column_letter

    results: dict[str, list[dict]] = {}
    if not zipfile.is_zipfile(file_path):
        return results

    try:
        with zipfile.ZipFile(file_path, "r") as z:
            names = set(z.namelist())
            if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
                return results

            # 1. Map sheet r_id to sheet name
            wb_root = ET.fromstring(z.read("xl/workbook.xml"))
            sheets_info = []
            for s in wb_root.iter():
                if s.tag.endswith("}sheet"):
                    name = s.attrib.get("name")
                    r_id = s.attrib.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    if name and r_id:
                        sheets_info.append((name, r_id))

            # 2. Map r_id to worksheet XML target
            wb_rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            rid_to_target = {}
            for rel in wb_rels_root.iter():
                if rel.tag.endswith("}Relationship"):
                    rid_to_target[rel.attrib.get("Id")] = rel.attrib.get("Target")

            sheet_to_xml = {}
            for name, r_id in sheets_info:
                target = rid_to_target.get(r_id)
                if target:
                    sheet_to_xml[name] = (
                        "xl/" + target if not target.startswith("xl/") else target
                    )

            # 3. For each sheet, locate drawings and extract shapes / images
            for sheet_name, sheet_xml in sheet_to_xml.items():
                if "/" not in sheet_xml:
                    continue
                dir_name, base_name = sheet_xml.rsplit("/", 1)
                rels_file = f"{dir_name}/_rels/{base_name}.rels"
                if rels_file not in names:
                    continue

                s_rels_root = ET.fromstring(z.read(rels_file))
                drawing_files = []
                for rel in s_rels_root.iter():
                    if rel.tag.endswith("}Relationship") and "drawing" in rel.attrib.get("Type", ""):
                        dt = rel.attrib.get("Target")
                        if dt:
                            norm = os.path.normpath(f"{dir_name}/{dt}")
                            drawing_files.append(norm)

                sheet_items: list[dict] = []
                for d_file in drawing_files:
                    if d_file not in names:
                        continue

                    # Read drawing rels for images
                    d_dir, d_base = d_file.rsplit("/", 1)
                    d_rels_file = f"{d_dir}/_rels/{d_base}.rels"
                    d_rid_to_target = {}
                    if d_rels_file in names:
                        d_rels_root = ET.fromstring(z.read(d_rels_file))
                        for rel in d_rels_root.iter():
                            if rel.tag.endswith("}Relationship"):
                                d_rid_to_target[rel.attrib.get("Id")] = rel.attrib.get("Target")

                    d_root = ET.fromstring(z.read(d_file))

                    # 1. Index all shapes in drawing XML for flowchart connector resolution
                    shapes_map: dict[str, dict] = {}
                    for anchor in d_root:
                        c1, r1, c2, r2 = 0, 0, 0, 0
                        from_el, to_el = None, None
                        for child in anchor:
                            if child.tag.endswith("}from"):
                                from_el = child
                            elif child.tag.endswith("}to"):
                                to_el = child
                        if from_el is not None:
                            c1_t, r1_t = None, None
                            for c in from_el:
                                if c.tag.endswith("}col"):
                                    c1_t = c.text
                                elif c.tag.endswith("}row"):
                                    r1_t = c.text
                            c1 = int(c1_t) if c1_t and c1_t.isdigit() else 0
                            r1 = int(r1_t) if r1_t and r1_t.isdigit() else 0
                        if to_el is not None:
                            c2_t, r2_t = None, None
                            for c in to_el:
                                if c.tag.endswith("}col"):
                                    c2_t = c.text
                                elif c.tag.endswith("}row"):
                                    r2_t = c.text
                            c2 = int(c2_t) if c2_t and c2_t.isdigit() else c1
                            r2 = int(r2_t) if r2_t and r2_t.isdigit() else r1

                        for sp in anchor.iter():
                            if sp.tag.endswith("}sp"):
                                cnv = None
                                for c in sp.iter():
                                    if c.tag.endswith("}cNvPr"):
                                        cnv = c
                                        break
                                if cnv is not None:
                                    sid = cnv.attrib.get("id")
                                    sname = cnv.attrib.get("name", "")
                                    p_texts = []
                                    for p in sp.iter():
                                        if p.tag.endswith("}p"):
                                            pt = "".join(
                                                t.text
                                                for t in p.iter()
                                                if t.tag.endswith("}t") and t.text
                                            ).strip()
                                            if pt:
                                                p_texts.append(pt)
                                    text = " / ".join(p_texts)
                                    if sid:
                                        shapes_map[sid] = {
                                            "id": sid,
                                            "name": sname,
                                            "text": text,
                                            "bounds": (c1, r1, max(c1, c2), max(r1, r2)),
                                            "loc": f"{get_column_letter(c1+1)}{r1+1}" if (c1 or r1) else "",
                                        }

                    # 2. Extract shapes, pictures, and connectors
                    for anchor in d_root:
                        loc_desc = "khu vực hình vẽ"
                        sort_key = (999999, 999999)
                        from_el, to_el = None, None
                        for child in anchor:
                            if child.tag.endswith("}from"):
                                from_el = child
                            elif child.tag.endswith("}to"):
                                to_el = child

                        c_c1, c_r1, c_c2, c_r2 = 0, 0, 0, 0
                        if from_el is not None:
                            col_text, row_text = None, None
                            for c in from_el:
                                if c.tag.endswith("}col"):
                                    col_text = c.text
                                elif c.tag.endswith("}row"):
                                    row_text = c.text
                            if col_text and row_text and col_text.isdigit() and row_text.isdigit():
                                c_c1 = int(col_text)
                                c_r1 = int(row_text)
                                sort_key = (c_r1, c_c1)
                                c_c2, c_r2 = c_c1, c_r1

                        if to_el is not None:
                            c2_t, r2_t = None, None
                            for c in to_el:
                                if c.tag.endswith("}col"):
                                    c2_t = c.text
                                elif c.tag.endswith("}row"):
                                    r2_t = c.text
                            c_c2 = int(c2_t) if c2_t and c2_t.isdigit() else c_c1
                            c_r2 = int(r2_t) if r2_t and r2_t.isdigit() else c_r1

                        if from_el is not None:
                            if c_c2 > c_c1 or c_r2 > c_r1:
                                loc_desc = f"khu vực ô {get_column_letter(c_c1 + 1)}{c_r1 + 1}:{get_column_letter(c_c2 + 1)}{c_r2 + 1}"
                            else:
                                loc_desc = f"khu vực ô {get_column_letter(c_c1 + 1)}{c_r1 + 1}"

                        # A. Extract text from shapes (<sp>) inside anchor
                        sps = [el for el in anchor.iter() if el.tag.endswith("}sp")]
                        for sp in sps:
                            sp_fill_tag = None
                            for spPr in sp.iter():
                                if spPr.tag.endswith("}spPr"):
                                    for srgb in spPr.iter():
                                        if srgb.tag.endswith("}srgbClr"):
                                            sp_fill_tag = classify_color_hex(
                                                srgb.attrib.get("val"), is_font=False
                                            )
                                            if sp_fill_tag:
                                                break
                                    if sp_fill_tag:
                                        break

                            tx_bodies = [el for el in sp.iter() if el.tag.endswith("}txBody")]
                            for tx_body in tx_bodies:
                                para_texts = []
                                for p in tx_body.iter():
                                    if p.tag.endswith("}p"):
                                        p_parts = []
                                        for child in p:
                                            c_tag = child.tag.split("}")[-1]
                                            if c_tag == "r":
                                                rPr = None
                                                for sub in child:
                                                    if sub.tag.endswith("}rPr"):
                                                        rPr = sub
                                                        break
                                                r_strike = False
                                                r_color_tag = None
                                                if rPr is not None:
                                                    strike_attr = rPr.attrib.get("strike")
                                                    if strike_attr and strike_attr not in ("none", "noStrike"):
                                                        r_strike = True
                                                    for srgb in rPr.iter():
                                                        if srgb.tag.endswith("}srgbClr"):
                                                            r_color_tag = classify_color_hex(
                                                                srgb.attrib.get("val"), is_font=True
                                                            )
                                                            if r_color_tag:
                                                                break
                                                t_text = "".join(
                                                    t.text
                                                    for t in child.iter()
                                                    if t.tag.endswith("}t") and t.text
                                                )
                                                if t_text:
                                                    if r_strike:
                                                        t_text = f"~~{t_text}~~"
                                                    elif r_color_tag:
                                                        t_text = f"{r_color_tag} {t_text}"
                                                    p_parts.append(t_text)
                                            elif c_tag == "br":
                                                p_parts.append("\n")
                                            elif c_tag == "t" and child.text:
                                                p_parts.append(child.text)
                                        p_text = "".join(p_parts).strip()
                                        if p_text:
                                            para_texts.append(p_text)
                                if para_texts:
                                    shape_text = "\n".join(para_texts)
                                    if sp_fill_tag and not shape_text.startswith(sp_fill_tag):
                                        shape_text = f"{sp_fill_tag} {shape_text}"
                                    sheet_items.append({
                                        "sort_key": sort_key,
                                        "type": "shape",
                                        "loc_desc": loc_desc,
                                        "text": shape_text,
                                    })

                        # B. Extract pictures (<pic>) inside anchor
                        pics = [el for el in anchor.iter() if el.tag.endswith("}pic")]
                        for pic in pics:
                            blips = [el for el in pic.iter() if el.tag.endswith("}blip")]
                            for blip in blips:
                                embed_id = None
                                for k, v in blip.attrib.items():
                                    if k.endswith("}embed") or k == "embed":
                                        embed_id = v
                                        break
                                if embed_id and embed_id in d_rid_to_target:
                                    img_target = d_rid_to_target[embed_id]
                                    img_path = os.path.normpath(f"{d_dir}/{img_target}")
                                    if img_path in names:
                                        img_bytes = z.read(img_path)
                                        sheet_items.append({
                                            "sort_key": sort_key,
                                            "type": "image",
                                            "loc_desc": loc_desc,
                                            "image_bytes": img_bytes,
                                        })

                        # C. Extract flowchart connectors (<cxnSp>) inside anchor
                        cxns = [el for el in anchor.iter() if el.tag.endswith("}cxnSp")]
                        for cxn in cxns:
                            cnv = None
                            for c in cxn.iter():
                                if c.tag.endswith("}cNvPr"):
                                    cnv = c
                                    break
                            c_name = cnv.attrib.get("name", "Connector") if cnv is not None else "Connector"

                            head, tail = None, None
                            for el in cxn.iter():
                                if el.tag.endswith("}headEnd"):
                                    head = el.attrib.get("type", "none")
                                elif el.tag.endswith("}tailEnd"):
                                    tail = el.attrib.get("type", "none")

                            arrow = "->"
                            if head not in (None, "none") and tail not in (None, "none"):
                                arrow = "<->"
                            elif head not in (None, "none"):
                                arrow = "<-"

                            st_id, end_id = None, None
                            for c in cxn.iter():
                                if c.tag.endswith("}stCxn"):
                                    st_id = c.attrib.get("id")
                                elif c.tag.endswith("}endCxn"):
                                    end_id = c.attrib.get("id")

                            # Coordinate calculation fallback if not snapped by ID
                            if not st_id and shapes_map:
                                closest = min(
                                    shapes_map.values(),
                                    key=lambda s: _point_to_box_dist(c_c1, c_r1, *s["bounds"]),
                                )
                                st_id = closest["id"]
                            if not end_id and shapes_map:
                                closest = min(
                                    shapes_map.values(),
                                    key=lambda s: _point_to_box_dist(c_c2, c_r2, *s["bounds"]),
                                )
                                end_id = closest["id"]

                            src = shapes_map.get(st_id)
                            dst = shapes_map.get(end_id)
                            if src and dst and src["id"] != dst["id"]:
                                src_lbl = _clean_flow_label(src["text"], src["name"])
                                dst_lbl = _clean_flow_label(dst["text"], dst["name"])
                                loc_info = (
                                    f" (từ ô {get_column_letter(c_c1+1)}{c_r1+1} đến ô {get_column_letter(c_c2+1)}{c_r2+1})"
                                    if (c_c1 or c_r1 or c_c2 or c_r2)
                                    else ""
                                )
                                flow_line = f'- {src_lbl} {arrow} {dst_lbl} via connector "{c_name}"{loc_info}'
                                sheet_items.append({
                                    "sort_key": (min(c_r1, c_r2), min(c_c1, c_c2)),
                                    "type": "flow",
                                    "text": flow_line,
                                })

                if sheet_items:
                    sheet_items.sort(key=lambda x: x["sort_key"])
                    results[sheet_name] = sheet_items

    except Exception as e:
        logger.warning(f"Error extracting XLSX drawings from {file_path}: {e}")

    return results


async def extract_xlsx_with_images(
    file_path: str, max_rows: int = 5000, max_cols: int = 50
) -> str:
    """
    Extract content from XLSX file, converting sheet tables to Markdown,
    extracting shapes, text boxes, and flowcharts with cell coordinates,
    and running OCR on embedded images with sheet and cell anchor context.
    """
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    def _load_wb():
        return load_workbook(file_path, data_only=True)

    wb = await asyncio.to_thread(_load_wb)
    drawings_by_sheet = await asyncio.to_thread(_extract_xlsx_drawings, file_path)
    content_parts: List[str] = []
    seen_image_hashes: set[str] = set()
    seen_shape_blocks: set[tuple] = set()

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        content_parts.append(f"\n# Sheet: {sheet_name}\n")

        # 1. Process embedded images on this sheet (openpyxl ws._images)
        if hasattr(ws, "_images") and ws._images:
            for img_obj in ws._images:
                anchor_desc = "khu vực bảng tính"
                if hasattr(img_obj, "anchor"):
                    anchor = img_obj.anchor
                    if (
                        hasattr(anchor, "_from")
                        and hasattr(anchor._from, "row")
                        and hasattr(anchor._from, "col")
                    ):
                        col_letter = get_column_letter(anchor._from.col + 1)
                        row_num = anchor._from.row + 1
                        anchor_desc = f"khu vực ô {col_letter}{row_num}"

                if hasattr(img_obj, "_data"):
                    try:
                        img_bytes = img_obj._data()
                        img_hash = hashlib.sha256(img_bytes).hexdigest()
                        if img_hash not in seen_image_hashes:
                            seen_image_hashes.add(img_hash)
                            loc_label = f"Sheet '{sheet_name}', {anchor_desc}"
                            ocr_res = await ocr_image_bytes(img_bytes, "image/png")
                            if ocr_res:
                                content_parts.append(
                                    format_image_ocr_callout(loc_label, ocr_res)
                                )
                    except Exception as e:
                        logger.warning(
                            f"Failed to OCR image in Excel sheet {sheet_name}: {e}"
                        )

        # 2. Process DrawingML drawings, shapes & flowcharts on this sheet
        sheet_drawings = drawings_by_sheet.get(sheet_name, [])
        sheet_shapes = []
        sheet_flows = []
        for item in sheet_drawings:
            if item["type"] == "image":
                img_bytes = item["image_bytes"]
                img_hash = hashlib.sha256(img_bytes).hexdigest()
                if img_hash not in seen_image_hashes:
                    seen_image_hashes.add(img_hash)
                    try:
                        loc_label = f"Sheet '{sheet_name}', {item['loc_desc']}"
                        ocr_res = await ocr_image_bytes(img_bytes, "image/png")
                        if ocr_res:
                            content_parts.append(
                                format_image_ocr_callout(loc_label, ocr_res)
                            )
                    except Exception as e:
                        logger.warning(
                            f"Failed to OCR drawing image in Excel sheet {sheet_name}: {e}"
                        )
            elif item["type"] == "shape":
                sheet_shapes.append(item)
            elif item["type"] == "flow":
                sheet_flows.append(item["text"])

        # Output shapes if any exist on this sheet
        if sheet_shapes:
            from collections import OrderedDict

            shapes_by_loc: OrderedDict[str, list[str]] = OrderedDict()
            for s in sheet_shapes:
                shapes_by_loc.setdefault(s["loc_desc"], []).append(s["text"])

            time_pattern = re.compile(r"^\d{1,2}:\d{2}$")
            rendered_shape_blocks: list[str] = []

            for loc, texts in shapes_by_loc.items():
                counts: OrderedDict[str, int] = OrderedDict()
                for t in texts:
                    counts[t] = counts.get(t, 0) + 1

                # If this is a multi-item template shape block (>= 3 items) and has already been seen in an earlier sheet, skip duplicate stamp
                if len(counts) >= 3:
                    block_sig = tuple(sorted(counts.items()))
                    if block_sig in seen_shape_blocks:
                        continue
                    seen_shape_blocks.add(block_sig)

                time_items: list[str] = []
                aggregated_items: list[tuple] = []

                for t, cnt in counts.items():
                    if time_pattern.match(t.strip()) and cnt == 1:
                        time_items.append(t.strip())
                    else:
                        if len(time_items) >= 3:
                            aggregated_items.append(("timeline", time_items))
                            time_items = []
                        elif time_items:
                            for ti in time_items:
                                aggregated_items.append(("item", ti, 1))
                            time_items = []
                        aggregated_items.append(("item", t, cnt))

                if len(time_items) >= 3:
                    aggregated_items.append(("timeline", time_items))
                elif time_items:
                    for ti in time_items:
                        aggregated_items.append(("item", ti, 1))

                if (
                    len(aggregated_items) == 1
                    and aggregated_items[0][0] == "item"
                    and aggregated_items[0][2] == 1
                ):
                    t = aggregated_items[0][1]
                    t_lines = t.strip().splitlines()
                    if len(t_lines) == 1:
                        rendered_shape_blocks.append(f"> 📌 **[{loc}]**: {t_lines[0]}")
                    else:
                        quoted = "\n".join(f"> {l}" if l.strip() else ">" for l in t_lines)
                        rendered_shape_blocks.append(f"> 📌 **[{loc}]**:\n{quoted}")
                elif aggregated_items:
                    block_lines = [f"> 📌 **[{loc}]**:"]
                    for kind, *args in aggregated_items:
                        if kind == "timeline":
                            t_list = args[0]
                            block_lines.append(f"> - ⏱️ **Mốc thời gian:** {' | '.join(t_list)}")
                        else:
                            t, cnt = args[0], args[1]
                            cnt_str = f" *(lặp lại {cnt} lần)*" if cnt > 1 else ""
                            t_lines = t.strip().splitlines()
                            if len(t_lines) == 1:
                                block_lines.append(f"> - {t_lines[0]}{cnt_str}")
                            else:
                                block_lines.append(f"> - {t_lines[0]}{cnt_str}")
                                for sub in t_lines[1:]:
                                    block_lines.append(f">   {sub}")
                    rendered_shape_blocks.append("\n".join(block_lines))

            if rendered_shape_blocks:
                content_parts.append("\n### 📝 Nội dung Hình vẽ & Khung văn bản (Shapes / Diagrams):\n")
                content_parts.extend(rendered_shape_blocks)

        # Output flowchart connections if any exist on this sheet
        if sheet_flows:
            content_parts.append("\n### 🔄 Luồng quy trình & Lưu đồ (Flowchart & Connections):\n")
            for flow_line in sheet_flows:
                content_parts.append(flow_line)

        # 3. Process tabular data with generalized merged-cell resolution and multi-table segmentation
        max_r = min(ws.max_row or 0, max_rows)
        max_c = min(ws.max_column or 0, max_cols)

        if max_r > 0 and max_c > 0:
            sheet_comments: List[str] = []

            # 3.1 Collect cell comments
            for r in range(1, max_r + 1):
                for c in range(1, max_c + 1):
                    cell = ws.cell(row=r, column=c)
                    if cell.comment and cell.comment.text:
                        c_author = (cell.comment.author or "").strip()
                        author_str = f" ({c_author})" if c_author else ""
                        c_coord = cell.coordinate
                        c_txt = (
                            cell.comment.text.strip()
                            .replace("\r\n", " ")
                            .replace("\n", " ")
                        )
                        if c_txt:
                            comment_entry = f"> - **Ô {c_coord}{author_str}**: {c_txt}"
                            if comment_entry not in sheet_comments:
                                sheet_comments.append(comment_entry)

            # 3.2 Pre-process merged cell ranges
            banner_rows: Dict[int, str] = {}
            h_merges: Dict[Tuple[int, int], Any] = {}
            h_continuations: Set[Tuple[int, int]] = set()

            for rng in ws.merged_cells.ranges:
                r1, r2, c1, c2 = rng.min_row, rng.max_row, rng.min_col, rng.max_col
                # Full-width banner or note spanning >= 15 cols or full sheet on a single row
                if (c2 - c1 >= 15 or c2 >= max_c) and c1 == 1 and r1 == r2:
                    val = ws.cell(r1, c1).value
                    if val is not None:
                        banner_rows[r1] = str(val).strip().replace("\r\n", " ").replace("\n", " ")
                    continue

                top_val = ws.cell(r1, c1).value
                if top_val is not None:
                    if c2 > c1:
                        # Record horizontal merge for hierarchical headers & mark continuation cells
                        for r in range(r1, r2 + 1):
                            for c in range(c1, c2 + 1):
                                h_merges[(r, c)] = top_val
                                if c > c1:
                                    h_continuations.add((r, c))

            def _get_cell_str(r: int, c: int, allow_h_merge: bool = False) -> str:
                if (r, c) in h_continuations and not allow_h_merge:
                    return ""
                val = None
                if allow_h_merge and (r, c) in h_merges:
                    val = h_merges[(r, c)]
                else:
                    val = ws.cell(r, c).value

                if val is None:
                    return ""
                cell_str = str(val).strip().replace("\r\n", " ").replace("\n", " ")
                if not cell_str:
                    return ""
                # Escape markdown pipes so cell content never corrupts table column structures
                cell_str = cell_str.replace("|", r"\|")

                # Formatting badges
                cell = ws.cell(r, c)
                is_strike = bool(cell.font and getattr(cell.font, "strike", False))
                if is_strike:
                    cell_str = f"~~{cell_str}~~"
                else:
                    fill_hex = (
                        extract_openpyxl_color_hex(getattr(cell.fill, "fgColor", None))
                        if cell.fill
                        else None
                    )
                    badge = classify_color_hex(fill_hex, is_font=False)
                    if not badge and cell.font:
                        font_hex = extract_openpyxl_color_hex(getattr(cell.font, "color", None))
                        badge = classify_color_hex(font_hex, is_font=True)
                    if badge and not cell_str.startswith(badge):
                        cell_str = f"{badge} {cell_str}"
                return cell_str

            def _is_header_row(row_cells: List[Tuple[int, str]]) -> bool:
                if len(row_cells) < 2:
                    return False
                int_count = sum(1 for _, v in row_cells if v.isdigit())
                if int_count > 0:
                    return False
                short_count = sum(1 for _, v in row_cells if len(v) < 40 and "\n" not in v)
                return short_count >= len(row_cells) * 0.8

            # 3.3 Scan rows and construct structured tables & section callouts
            current_table_rows: List[int] = []
            active_header_cols: Optional[List[int]] = None
            active_header_names: Optional[List[str]] = None

            def flush_table():
                nonlocal current_table_rows, active_header_cols, active_header_names
                if not current_table_rows:
                    return

                # Determine active columns for this table
                cols = list(active_header_cols) if active_header_cols else []
                if not cols:
                    for c in range(1, max_c + 1):
                        if any(_get_cell_str(r, c) for r in current_table_rows):
                            cols.append(c)
                if not cols:
                    current_table_rows = []
                    return

                # If only 1 column, output as plain paragraphs rather than a single-column table
                if len(cols) == 1:
                    lines = [
                        _get_cell_str(r, cols[0])
                        for r in current_table_rows
                    ]
                    lines = [l for l in lines if l]
                    if lines:
                        content_parts.append("\n" + "\n\n".join(lines) + "\n")
                    current_table_rows = []
                    return

                # If only 1 row and 1-2 cells outside a structured table, output as plain text
                if len(current_table_rows) == 1 and not active_header_names:
                    r_single = current_table_rows[0]
                    cells = [
                        _get_cell_str(r_single, c)
                        for c in cols
                        if _get_cell_str(r_single, c)
                    ]
                    if len(cells) <= 2:
                        content_parts.append("\n" + " : ".join(cells) + "\n")
                        current_table_rows = []
                        return

                # Headers
                hdrs = list(active_header_names) if active_header_names else []
                data_rows = list(current_table_rows)
                if not hdrs:
                    r0 = data_rows[0]
                    r0_cells = [
                        (c, _get_cell_str(r0, c))
                        for c in cols
                        if _get_cell_str(r0, c)
                    ]
                    if _is_header_row(r0_cells):
                        data_rows = data_rows[1:]
                        hdrs = [
                            _get_cell_str(r0, c, allow_h_merge=True)
                            or f"Col_{get_column_letter(c)}"
                            for c in cols
                        ]
                    else:
                        hdrs = [f"Col_{get_column_letter(c)}" for c in cols]

                table_lines = [
                    "| " + " | ".join(hdrs) + " |",
                    "| " + " | ".join(["---"] * len(hdrs)) + " |",
                ]
                for r in data_rows:
                    row_vals = [
                        _get_cell_str(r, c, allow_h_merge=False)
                        for c in cols
                    ]
                    if any(row_vals):
                        table_lines.append("| " + " | ".join(row_vals) + " |")

                if len(table_lines) > 2:
                    content_parts.append("\n" + "\n".join(table_lines) + "\n")
                current_table_rows = []

            r = 1
            header_just_set = False
            while r <= max_r:
                if r in banner_rows:
                    flush_table()
                    header_just_set = False
                    content_parts.append(f"\n> 📌 **[{banner_rows[r]}]**\n")
                    r += 1
                    continue

                raw_cells = [
                    (c, _get_cell_str(r, c))
                    for c in range(1, max_c + 1)
                ]
                raw_non_empty = [(c, v) for c, v in raw_cells if v]

                if not raw_non_empty:
                    # Empty row flushes current table but preserves active header
                    flush_table()
                    header_just_set = False
                    r += 1
                    continue

                # Single-cell heading outside table
                if len(raw_non_empty) == 1 and raw_non_empty[0][0] <= 3 and not current_table_rows:
                    txt = raw_non_empty[0][1]
                    if (
                        re.match(r"^[0-9]+[\.．]", txt)
                        or txt.startswith("■")
                        or txt.startswith("【")
                        or txt.startswith("※")
                        or txt.startswith("#")
                    ):
                        flush_table()
                        header_just_set = False
                        content_parts.append(f"\n### {txt}\n")
                        r += 1
                        continue

                # Check if 2-level hierarchical header starting at r:
                # Requires row r has horizontal merges spanning >= 2 columns that cover >= 2 distinct sub-headers in row r+1.
                is_2level_header = False
                if not header_just_set and r + 1 <= max_r and _is_header_row(raw_non_empty) and not current_table_rows:
                    r1_cells = [
                        (c, _get_cell_str(r + 1, c, allow_h_merge=False))
                        for c in range(1, max_c + 1)
                        if _get_cell_str(r + 1, c, allow_h_merge=False)
                    ]
                    if _is_header_row(r1_cells):
                        for rng in ws.merged_cells.ranges:
                            if rng.min_row <= r <= rng.max_row and rng.max_col > rng.min_col:
                                sub_count = sum(
                                    1 for c, _ in r1_cells if rng.min_col <= c <= rng.max_col
                                )
                                if sub_count >= 2:
                                    is_2level_header = True
                                    break

                if is_2level_header:
                    flush_table()
                    r1_cells = [
                        (c, _get_cell_str(r + 1, c, allow_h_merge=False))
                        for c in range(1, max_c + 1)
                        if _get_cell_str(r + 1, c, allow_h_merge=False)
                    ]
                    active_cols = sorted(
                        list(set([c for c, _ in raw_non_empty] + [c for c, _ in r1_cells]))
                    )
                    active_header_cols = active_cols
                    hdrs = []
                    for c in active_cols:
                        v0 = _get_cell_str(r, c, allow_h_merge=True)
                        v1 = _get_cell_str(r + 1, c, allow_h_merge=False)
                        if v0 and v1 and v0 != v1:
                            hdrs.append(f"{v0} > {v1}")
                        elif v0:
                            hdrs.append(v0)
                        elif v1:
                            hdrs.append(v1)
                        else:
                            hdrs.append(f"Col_{get_column_letter(c)}")
                    active_header_names = hdrs
                    header_just_set = True
                    r += 2
                    continue

                # Check if 1-level header
                if (
                    not header_just_set
                    and _is_header_row(raw_non_empty)
                    and len(raw_non_empty) >= 2
                    and not current_table_rows
                ):
                    flush_table()
                    active_cols = [c for c, _ in raw_non_empty]
                    active_header_cols = active_cols
                    active_header_names = [
                        _get_cell_str(r, c, allow_h_merge=True)
                        for c in active_cols
                    ]
                    header_just_set = True
                    r += 1
                    continue

                current_table_rows.append(r)
                header_just_set = False
                r += 1

            flush_table()

            if sheet_comments:
                content_parts.append(
                    "\n### 💬 Ghi chú ô tính (Cell Comments):\n"
                    + "\n".join(sheet_comments)
                    + "\n"
                )

    return "\n\n".join(content_parts)


# ---------------------------------------------------------------------------
# PDF (.pdf) Extraction
# ---------------------------------------------------------------------------

def _clean_pdf_page_noise(text: str) -> str:
    """Remove repetitive running footers and standalone page numbers from PDF page text."""
    cleaned = re.sub(
        r"(?i)GOOD\s*PEOPLE\s*MAKE\s*GOOD\s*PRODUCTS[–\s\-]*PAGE\s*\d+",
        "",
        text,
    )
    cleaned = re.sub(r"(?im)^\s*PAGE\s*\d+\s*$", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*[-—]\s*\d+\s*[-—]\s*$", "", cleaned)
    return cleaned.strip()


async def extract_pdf_with_images(file_path: str) -> str:
    """
    Extract content from PDF file, extracting text, tables, annotations (strikethroughs, comments, highlights),
    and running OCR on embedded images with page number context.
    """
    import pdfplumber

    def _process_pdf():
        pages_content: List[
            Tuple[
                int,
                str,
                List[List[List[str]]],
                List[Tuple[bytes, Tuple]],
                List[str],
            ]
        ] = []
        with pdfplumber.open(file_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                tables = []
                try:
                    raw_tables = page.extract_tables() or []
                    for t in raw_tables:
                        if t and any(any(str(c).strip() for c in r if c) for r in t if r):
                            tables.append(t)
                except Exception as e:
                    logger.debug(f"Table extraction failed on PDF page {page_idx}: {e}")

                images_data = []
                if hasattr(page, "images") and page.images:
                    for im_info in page.images:
                        w = im_info.get("width", 0)
                        h = im_info.get("height", 0)
                        # Filter out tiny decorative icons / divider lines
                        if w < 70 or h < 70:
                            continue
                        try:
                            bbox = (
                                im_info["x0"],
                                im_info["top"],
                                im_info["x1"],
                                im_info["bottom"],
                            )
                            cropped = page.crop(bbox)
                            pil_im = cropped.to_image(resolution=150).original
                            buf = io.BytesIO()
                            pil_im.save(buf, format="PNG")
                            images_data.append((buf.getvalue(), bbox))
                        except Exception as e:
                            logger.debug(
                                f"Failed to crop image on page {page_idx}: {e}"
                            )

                # Extract page annotations (strikethroughs, comments, highlights)
                annotations: List[str] = []
                try:
                    raw_annots = getattr(page, "annots", None) or []
                    for annot in raw_annots:
                        data = annot.get("data", {})
                        subtype = str(data.get("Subtype", "")).replace("/", "")
                        contents = (annot.get("contents") or "").strip()
                        author = annot.get("title") or "Người nhận xét"

                        if subtype == "StrikeOut":
                            try:
                                bbox = (
                                    annot["x0"],
                                    annot["top"],
                                    annot["x1"],
                                    annot["bottom"],
                                )
                                if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                                    struck = (page.crop(bbox).extract_text() or "").strip()
                                    if struck:
                                        annotations.append(
                                            f"> - **Gạch bỏ (StrikeOut)**: ~~{struck}~~"
                                        )
                                    elif contents:
                                        annotations.append(
                                            f"> - **Gạch bỏ (StrikeOut)**: ~~{contents}~~"
                                        )
                            except Exception:
                                if contents:
                                    annotations.append(
                                        f"> - **Gạch bỏ (StrikeOut)**: ~~{contents}~~"
                                    )
                        elif subtype in ("Text", "FreeText"):
                            if contents:
                                annotations.append(
                                    f"> - **💬 Ghi chú [{author}]**: {contents}"
                                )
                        elif subtype == "Highlight":
                            if contents:
                                annotations.append(
                                    f"> - **Highlight [{author}]**: {contents}"
                                )
                except Exception as e:
                    logger.debug(
                        f"Annotation extraction failed on PDF page {page_idx}: {e}"
                    )

                pages_content.append((page_idx, text, tables, images_data, annotations))
        return pages_content

    extracted_pages = await asyncio.to_thread(_process_pdf)
    content_parts: List[str] = []
    seen_image_hashes: Set[str] = set()

    for page_idx, page_text, tables, images_data, annotations in extracted_pages:
        loc_label = f"Trang {page_idx}"
        content_parts.append(f"\n## {loc_label}\n")

        cleaned_text = _clean_pdf_page_noise(page_text)
        if cleaned_text:
            content_parts.append(cleaned_text)

        # Render tables
        for tbl in tables:
            num_cols = max(len(r) for r in tbl)
            padded = [
                [str(c or "").strip().replace("\n", " ") for c in r]
                + [""] * (num_cols - len(r))
                for r in tbl
            ]
            if padded:
                md_tbl = [
                    "| " + " | ".join(padded[0]) + " |",
                    "| " + " | ".join(["---"] * num_cols) + " |",
                ]
                for r in padded[1:]:
                    md_tbl.append("| " + " | ".join(r) + " |")
                content_parts.append("\n" + "\n".join(md_tbl) + "\n")

        # Render page annotations (StrikeOut, Comments, Highlights)
        if annotations:
            content_parts.append(
                "\n### 📝 Ghi chú & Nhận xét trên trang (Page Annotations):\n"
                + "\n".join(annotations)
                + "\n"
            )

        # OCR embedded images
        for img_bytes, _ in images_data:
            img_hash = hashlib.sha256(img_bytes).hexdigest()
            if img_hash in seen_image_hashes:
                continue
            seen_image_hashes.add(img_hash)
            try:
                ocr_res = await ocr_image_bytes(img_bytes, "image/png")
                if ocr_res:
                    content_parts.append(
                        format_image_ocr_callout(loc_label, ocr_res)
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to OCR image on PDF {loc_label}: {e}"
                )

    return "\n\n".join(content_parts)


# ---------------------------------------------------------------------------
# Main Hybrid Document Dispatcher
# ---------------------------------------------------------------------------

async def extract_document_content_hybrid(
    file_path: str, mime_type: Optional[str] = None
) -> str:
    """
    Main dispatcher for extracting documents with embedded image OCR.
    Dispatches according to file extension or MIME type, and cleans the final Markdown.
    """
    check_encrypted_office_file(file_path)

    ext = os.path.splitext(file_path)[1].lower()
    mime = (mime_type or "").lower()

    if ext == ".docx" or "wordprocessingml" in mime:
        raw = await extract_docx_with_images(file_path)
    elif ext == ".pptx" or "presentationml" in mime:
        raw = await extract_pptx_with_images(file_path)
    elif ext == ".xlsx" or "spreadsheetml" in mime:
        raw = await extract_xlsx_with_images(file_path)
    elif ext == ".pdf" or mime == "application/pdf":
        raw = await extract_pdf_with_images(file_path)
    else:
        raise ValueError(f"Unsupported document format for embedded image OCR: {file_path}")

    return clean_markdown_content(raw)
