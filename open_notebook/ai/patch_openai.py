"""Patch langchain_openai to preserve reasoning_content (thinking tokens).

OpenAI-compatible models (e.g., DeepSeek-R1, Qwen 2.5/3.6 think, vLLM) return
thinking process inside `reasoning_content` on messages and streaming deltas.
Standard langchain_openai (<= 1.1.14) drops this field when converting dicts
to BaseMessage / BaseMessageChunk. This patch preserves it in `additional_kwargs`.
"""

from typing import Any, Mapping
from loguru import logger

_PATCHED = False


def apply_openai_reasoning_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        import langchain_openai.chat_models.base as base

        # 1. Patch full message conversion
        orig_convert_dict_to_message = base._convert_dict_to_message

        def patched_convert_dict_to_message(_dict: Mapping[str, Any]) -> Any:
            msg = orig_convert_dict_to_message(_dict)
            if _dict.get("role") == "assistant":
                rc = _dict.get("reasoning_content")
                if not rc and isinstance(_dict.get("provider_specific_fields"), dict):
                    rc = _dict["provider_specific_fields"].get("reasoning_content")
                if rc:
                    msg.additional_kwargs["thinking"] = rc
                    msg.additional_kwargs["reasoning_content"] = rc
            return msg

        base._convert_dict_to_message = patched_convert_dict_to_message

        # 2. Patch streaming delta conversion
        orig_convert_delta_to_message_chunk = base._convert_delta_to_message_chunk

        def patched_convert_delta_to_message_chunk(
            _dict: Mapping[str, Any], default_class: Any
        ) -> Any:
            chunk = orig_convert_delta_to_message_chunk(_dict, default_class)
            rc = _dict.get("reasoning_content")
            if not rc and isinstance(_dict.get("provider_specific_fields"), dict):
                rc = _dict["provider_specific_fields"].get("reasoning_content")
            if rc:
                chunk.additional_kwargs["thinking"] = rc
                chunk.additional_kwargs["reasoning_content"] = rc
            return chunk

        base._convert_delta_to_message_chunk = patched_convert_delta_to_message_chunk

        _PATCHED = True
        logger.debug("Applied langchain_openai reasoning_content patch successfully.")
    except Exception as e:
        logger.warning(f"Could not apply langchain_openai reasoning patch: {e}")


# Automatically apply when module is imported
apply_openai_reasoning_patch()
