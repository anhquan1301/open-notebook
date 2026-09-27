"""
OCR and Multimodal Vision extraction service for Open Notebook.

Supports Hybrid Architecture:
1. Primary: Vision LLM (Multimodal AI) - extracts structured text, tables, and formatting into GFM.
2. Fallback: Local OCR (RapidOCR / Tesseract) - works 100% offline on CPU.
"""

import asyncio
import base64
import mimetypes
import os
import re
from typing import Optional

from loguru import logger
from PIL import Image

from open_notebook.ai.models import ModelManager
from open_notebook.utils.text_utils import clean_markdown_content

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
}

SUPPORTED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
    "image/tiff",
}


def is_image_file(file_path: str, mime: Optional[str] = None) -> bool:
    """Check if the given file path or MIME type corresponds to a supported image."""
    if mime and mime.lower() in SUPPORTED_IMAGE_MIMES:
        return True

    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_IMAGE_EXTENSIONS


def _guess_image_mime(file_path: str) -> str:
    """Guess MIME type for an image file."""
    mime, _ = mimetypes.guess_type(file_path)
    if mime and mime.startswith("image/"):
        return mime
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    elif ext == ".webp":
        return "image/webp"
    elif ext in (".tiff", ".tif"):
        return "image/tiff"
    elif ext == ".bmp":
        return "image/bmp"
    return "image/png"


_VISION_REFUSAL_PATTERNS = [
    # English: statements that the model cannot process/see images or is text-only
    r"unable to perform ocr",
    r"don't have image processing",
    r"do not have image processing",
    r"cannot actually see the image",
    r"cannot see the image",
    r"can't see the image",
    r"cannot view images",
    r"unable to view images",
    r"cannot view the image",
    r"unable to see any image",
    r"no image was provided",
    r"no image provided",
    r"no image is provided",
    r"no image was attached",
    r"no image attached",
    r"no image is attached",
    r"don't see an attached image",
    r"do not see an attached image",
    r"only work with text input",
    r"i am a text-based",
    r"as a text-based",
    r"i am a language model",
    r"cannot extract text from images",
    r"please provide the image",
    r"please upload the image",
    r"please re-upload",
    r"without the source image",
    r"without seeing the image",
    r"garbled/corrupted ocr output",
    r"i cannot read images",
    r"i am unable to read images",
    r"i am unable to process images",
    # Vietnamese
    r"không có hình ảnh nào",
    r"không thể xem ảnh",
    r"không thể xem hình",
    r"không có khả năng xử lý hình ảnh",
    r"vui lòng gửi lại ảnh",
    r"vui lòng cung cấp hình ảnh",
    r"tôi là mô hình ngôn ngữ",
    r"chỉ làm việc với văn bản",
    r"không thể thực hiện ocr",
    r"không trích xuất được hình ảnh",
]

_REFUSAL_REGEXES = [re.compile(p, re.IGNORECASE) for p in _VISION_REFUSAL_PATTERNS]


def _is_vision_refusal(text: str) -> bool:
    """Check if the text is an AI refusal, inability to view image, or meta-commentary."""
    if not text:
        return False
    for regex in _REFUSAL_REGEXES:
        if regex.search(text):
            return True
    return False


def _clean_reasoning_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from reasoning/thinking models."""
    if not text:
        return text
    # Strip <think>...</think>
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


async def extract_text_with_vision(
    image_bytes: bytes, mime_type: str, model_id: Optional[str] = None
) -> Optional[str]:
    """Extract text and tables from image using a Vision-capable LLM."""
    try:
        mm = ModelManager()
        if not model_id:
            defaults = await mm.get_defaults()
            model_id = defaults.default_chat_model

        if not model_id:
            logger.debug("No default chat model configured for vision extraction.")
            return None

        model = await mm.get_model(model_id)
        if not model:
            logger.debug(f"Model {model_id} could not be resolved for vision.")
            return None

        lc_model = model.to_langchain()
        from langchain_core.messages import HumanMessage

        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{b64_data}"

        prompt = (
            "You are an expert document OCR and digitization system.\n"
            "Analyze the image carefully and extract all readable text, titles, sections, lists, tables, and notes into clean GitHub Flavored Markdown (GFM).\n"
            "Rules:\n"
            "- Faithfully transcribe all characters, words, numbers, and symbols.\n"
            "- Preserve languages accurately (including Vietnamese accents, Japanese kanji/kana, English, etc.).\n"
            "- Format tables using proper Markdown table syntax (| Header | and | --- |).\n"
            "- Do NOT hallucinate or summarize. Extract what is visible in the image.\n"
            "- Output ONLY the extracted Markdown content. Do not include introductory or concluding conversational text."
        )

        msg = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        )

        logger.info(f"Attempting Vision extraction with model {model_id}...")
        try:
            response = await asyncio.wait_for(lc_model.ainvoke([msg]), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning(
                f"Vision model extraction timed out after 60s for model {model_id}. Falling back to local OCR."
            )
            return None
        raw_text = response.content if hasattr(response, "content") else str(response)

        # Handle list of blocks or string response
        if isinstance(raw_text, list):
            text_blocks = []
            for block in raw_text:
                if isinstance(block, dict) and block.get("text"):
                    text_blocks.append(block["text"])
                else:
                    text_blocks.append(str(block))
            raw_text = "\n".join(text_blocks)

        raw_text = _clean_reasoning_tags(raw_text)
        if raw_text and raw_text.strip():
            if _is_vision_refusal(raw_text):
                logger.warning(
                    f"Vision model {model_id} returned a refusal or indicated inability to process image: "
                    f"'{raw_text[:120]}...'. Falling back to local OCR."
                )
                return None
            logger.info("Vision extraction succeeded.")
            return raw_text.strip()

        return None
    except Exception as e:
        logger.warning(f"Vision model extraction failed or model is text-only: {e}")
        return None


def extract_text_with_local_ocr(image_bytes: bytes) -> Optional[str]:
    """Extract text from image using local OCR engine (Tesseract with vie/jpn / RapidOCR fallback)."""
    # 1. Try pytesseract first if installed (supports vie, jpn, eng)
    try:
        import io
        import pytesseract

        available_langs = []
        try:
            available_langs = pytesseract.get_languages(config="")
        except Exception:
            pass

        if available_langs:
            langs_to_use = [l for l in ["vie", "jpn", "eng"] if l in available_langs]
            lang_param = "+".join(langs_to_use) if langs_to_use else "eng"

            img = Image.open(io.BytesIO(image_bytes))
            extracted = pytesseract.image_to_string(img, lang=lang_param)
            if extracted and extracted.strip():
                logger.info(f"Local Tesseract OCR ({lang_param}) succeeded.")
                return extracted.strip()
    except ImportError:
        logger.debug("pytesseract not installed.")
    except Exception as e:
        logger.warning(f"Tesseract extraction failed: {e}")

    # 2. Try RapidOCR (ONNX-based, fast, runs on CPU) as fallback
    try:
        from rapidocr_onnxruntime import RapidOCR

        ocr = RapidOCR()
        results, _ = ocr(image_bytes)
        if results:
            # Sort boxes primarily by vertical position (Y), then horizontal (X)
            sorted_boxes = sorted(results, key=lambda b: (b[0][0][1], b[0][0][0]))

            # Group lines that are vertically close (within 12px)
            lines = []
            current_line = []
            current_y = None
            y_threshold = 12

            for box in sorted_boxes:
                y = box[0][0][1]
                text = box[1].strip()
                if not text:
                    continue

                if current_y is None or abs(y - current_y) <= y_threshold:
                    current_line.append(text)
                    if current_y is None:
                        current_y = y
                else:
                    lines.append("  ".join(current_line))
                    current_line = [text]
                    current_y = y

            if current_line:
                lines.append("  ".join(current_line))

            extracted = "\n\n".join(lines)
            if extracted.strip():
                logger.info(f"Local RapidOCR succeeded: extracted {len(lines)} lines.")
                return extracted.strip()
    except ImportError:
        logger.debug("rapidocr_onnxruntime not installed.")
    except Exception as e:
        logger.warning(f"RapidOCR extraction failed: {e}")

    return None


async def ocr_image_bytes(
    image_bytes: bytes,
    mime_type: str = "image/png",
    min_width: int = 60,
    min_height: int = 60,
) -> Optional[str]:
    """
    Perform Hybrid OCR on raw image bytes.
    Filters out tiny images (icons, logos, decorations < min_width x min_height).
    Returns cleaned Markdown text or None if empty/blank/skipped.
    """
    if not image_bytes or len(image_bytes) < 100:
        return None

    try:
        import io

        with Image.open(io.BytesIO(image_bytes)) as pil_img:
            w, h = pil_img.size
            if w < min_width or h < min_height:
                logger.debug(
                    f"Skipping tiny image ({w}x{h} < {min_width}x{min_height})"
                )
                return None
    except Exception as e:
        logger.debug(f"Failed to inspect image dimensions: {e}")
        return None

    # Step 1: Try Vision LLM (if enabled in ContentSettings)
    vision_enabled = True
    try:
        from open_notebook.domain.content_settings import ContentSettings

        settings = await ContentSettings.get_instance()
        if settings and settings.vision_ocr is not None:
            vision_enabled = settings.vision_ocr
    except Exception as e:
        logger.debug(f"Could not load content settings for OCR: {e}")

    text = None
    if vision_enabled:
        text = await extract_text_with_vision(image_bytes, mime_type)
    else:
        logger.debug("Vision OCR is disabled in settings; skipping Vision LLM.")

    # Step 2: Fallback to Local OCR
    if not text:
        logger.debug(
            "Vision LLM did not return text for image bytes; falling back to local OCR..."
        )
        text = extract_text_with_local_ocr(image_bytes)

    if not text or not text.strip():
        return None

    return clean_markdown_content(text)


async def extract_image_content(
    file_path: str, mime_type: Optional[str] = None
) -> str:
    """
    Main entrypoint to extract Markdown text from an image file.

    Follows Hybrid Architecture:
    1. Attempts Vision LLM extraction.
    2. Falls back to Local OCR.
    3. Cleans and structures the resulting Markdown.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    mime = mime_type or _guess_image_mime(file_path)

    with open(file_path, "rb") as f:
        image_bytes = f.read()

    if not image_bytes:
        raise ValueError("Image file is empty")

    extracted_content = await ocr_image_bytes(
        image_bytes, mime, min_width=10, min_height=10
    )

    if not extracted_content or not extracted_content.strip():
        raise ValueError(
            "Could not extract any text from this image. "
            "The image may be blank, unreadable, or no OCR/Vision model is available."
        )

    return extracted_content

