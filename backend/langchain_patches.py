"""Monkey-patch langchain_openai to preserve reasoning_content for DeepSeek thinking models.

Problem: langchain_openai drops `reasoning_content` from API responses and doesn't
serialize it back to outgoing requests. DeepSeek's thinking models require this field
to be preserved in multi-turn conversations (including agent tool-call loops).

Patched functions:
- _convert_dict_to_message: store reasoning_content → AIMessage.additional_kwargs
- _convert_delta_to_message_chunk: store reasoning_content → AIMessageChunk.additional_kwargs
- _convert_message_to_dict: emit reasoning_content from additional_kwargs → request dict
"""

from typing import Any, Literal, Mapping

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import ToolCall


def apply_patches() -> None:
    import langchain_openai.chat_models.base as _base

    # ── Patch 1: capture reasoning_content from API response ──
    _orig_convert_dict_to_message = _base._convert_dict_to_message

    def _patched_convert_dict_to_message(_dict: Mapping[str, Any]) -> BaseMessage:
        msg = _orig_convert_dict_to_message(_dict)
        if isinstance(msg, AIMessage) and "reasoning_content" in _dict:
            msg.additional_kwargs["reasoning_content"] = _dict["reasoning_content"]
        return msg

    _base._convert_dict_to_message = _patched_convert_dict_to_message

    # ── Patch 2: capture reasoning_content from streaming delta ──
    _orig_convert_delta = _base._convert_delta_to_message_chunk

    def _patched_convert_delta_to_message_chunk(
        _dict: Mapping[str, Any], default_class: type
    ) -> BaseMessage:
        chunk = _orig_convert_delta(_dict, default_class)
        if isinstance(chunk, AIMessageChunk) and "reasoning_content" in _dict:
            chunk.additional_kwargs["reasoning_content"] = _dict["reasoning_content"]
        return chunk

    _base._convert_delta_to_message_chunk = _patched_convert_delta_to_message_chunk

    # ── Patch 3: emit reasoning_content in outgoing request ──
    _orig_convert_message_to_dict = _base._convert_message_to_dict

    def _patched_convert_message_to_dict(
        message: BaseMessage,
        api: Literal["chat/completions", "responses"] = "chat/completions",
    ) -> dict:
        msg_dict = _orig_convert_message_to_dict(message, api)
        if isinstance(message, AIMessage) and "reasoning_content" in message.additional_kwargs:
            msg_dict["reasoning_content"] = message.additional_kwargs["reasoning_content"]
        return msg_dict

    _base._convert_message_to_dict = _patched_convert_message_to_dict
