"""tool 반환값 헬퍼."""
from __future__ import annotations

from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel


def tool_result(model: BaseModel, text: str) -> CallToolResult:
    """구조화 결과와 한국어 요약 텍스트를 함께 반환."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=model.model_dump(mode="json"),
    )


def tool_error(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        is_error=True,
    )
