import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image, ImageDraw

from open_notebook.utils.ocr_service import (
    _is_vision_refusal,
    extract_image_content,
    extract_text_with_local_ocr,
    extract_text_with_vision,
    is_image_file,
    ocr_image_bytes,
)


class TestOCRService(unittest.IsolatedAsyncioTestCase):
    def test_is_image_file(self):
        self.assertTrue(is_image_file("photo.png"))
        self.assertTrue(is_image_file("test.jpg"))
        self.assertTrue(is_image_file("scan.JPEG"))
        self.assertTrue(is_image_file("document.webp"))
        self.assertTrue(is_image_file("file.bmp"))
        self.assertTrue(is_image_file("sample.tiff"))
        self.assertTrue(is_image_file("sample.tif"))
        self.assertFalse(is_image_file("doc.pdf"))
        self.assertFalse(is_image_file("data.xlsx"))
        self.assertFalse(is_image_file("notes.md"))
        self.assertFalse(is_image_file("audio.mp3"))
        self.assertTrue(is_image_file("unknown", mime="image/png"))
        self.assertFalse(is_image_file("unknown", mime="application/pdf"))

    async def test_extract_text_with_vision_success(self):
        img = Image.new("RGB", (100, 50), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.content = (
            "<think>analyzing image</think>\n# Invoice Title\n\nContent details"
        )

        mock_lc = MagicMock()
        mock_lc.ainvoke = AsyncMock(return_value=mock_resp)

        mock_model = MagicMock()
        mock_model.to_langchain.return_value = mock_lc

        with (
            patch(
                "open_notebook.ai.models.ModelManager.get_defaults",
                AsyncMock(
                    return_value=MagicMock(default_chat_model="model:test-vision")
                ),
            ),
            patch(
                "open_notebook.ai.models.ModelManager.get_model",
                AsyncMock(return_value=mock_model),
            ),
        ):
            result = await extract_text_with_vision(image_bytes, "image/png")
            self.assertIsNotNone(result)
            self.assertIn("# Invoice Title", result)
            self.assertNotIn("<think>", result)

    def test_extract_text_with_local_ocr(self):
        img = Image.new("RGB", (300, 80), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((10, 20), "RAPID OCR TEST 123", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        result = extract_text_with_local_ocr(image_bytes)
        self.assertIsNotNone(result)
        self.assertTrue(len(result.strip()) > 0)

    async def test_extract_image_content_hybrid_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_file = Path(tmp_dir) / "test.png"
            img = Image.new("RGB", (300, 80), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)
            draw.text((10, 20), "LOCAL FALLBACK TEST", fill=(0, 0, 0))
            img.save(img_file)

            # When vision fails/returns None, it should fall back to local OCR
            with patch(
                "open_notebook.utils.ocr_service.extract_text_with_vision",
                AsyncMock(return_value=None),
            ):
                content = await extract_image_content(str(img_file))
                self.assertIsNotNone(content)
                self.assertTrue(len(content.strip()) > 0)

    def test_is_vision_refusal(self):
        self.assertTrue(
            _is_vision_refusal(
                "I'm unable to perform OCR on this image. I don't have image processing capabilities in my current toolset."
            )
        )
        self.assertTrue(
            _is_vision_refusal(
                "Không có hình ảnh nào được đính kèm trong tin nhắn này. Vui lòng gửi lại ảnh."
            )
        )
        self.assertTrue(
            _is_vision_refusal(
                "Since I cannot actually see the image and the provided text is the only content, I'll transcribe..."
            )
        )
        self.assertFalse(_is_vision_refusal("# Invoice 123\n| Item | Price |\n| --- | --- |"))
        self.assertFalse(_is_vision_refusal("Smart Meter API documentation and roadmap."))

    async def test_extract_text_with_vision_refusal_returns_none(self):
        img = Image.new("RGB", (100, 50), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.content = (
            "I'm unable to perform OCR on this image. I don't have image processing capabilities."
        )

        mock_lc = MagicMock()
        mock_lc.ainvoke = AsyncMock(return_value=mock_resp)

        mock_model = MagicMock()
        mock_model.to_langchain.return_value = mock_lc

        with (
            patch(
                "open_notebook.ai.models.ModelManager.get_defaults",
                AsyncMock(
                    return_value=MagicMock(default_chat_model="model:test-vision")
                ),
            ),
            patch(
                "open_notebook.ai.models.ModelManager.get_model",
                AsyncMock(return_value=mock_model),
            ),
        ):
            result = await extract_text_with_vision(image_bytes, "image/png")
            # Should be None due to refusal detection!
            self.assertIsNone(result)

    async def test_ocr_image_bytes_vision_disabled(self):
        img = Image.new("RGB", (200, 80), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((10, 20), "LOCAL ONLY TEST", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        # Mock settings with vision_ocr=False
        mock_settings = MagicMock(vision_ocr=False)

        with (
            patch(
                "open_notebook.domain.content_settings.ContentSettings.get_instance",
                AsyncMock(return_value=mock_settings),
            ),
            patch(
                "open_notebook.utils.ocr_service.extract_text_with_vision",
                AsyncMock(),
            ) as mock_vision,
        ):
            result = await ocr_image_bytes(image_bytes)
            # Vision LLM should not even be called when vision_ocr=False!
            mock_vision.assert_not_called()
            self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
