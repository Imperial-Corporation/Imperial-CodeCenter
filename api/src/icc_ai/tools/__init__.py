"""Outils IA de CodeCenter."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolExecution, ToolLimits, ToolOutcome, ToolRegistry
from .search_tools import FileInfoTool, ListFiltersTool, ReadFileTool, SearchCodeTool


def build_default_registry() -> ToolRegistry:
    """Registre des outils disponibles. Ajouter un outil = une ligne ici."""
    registry = ToolRegistry()
    for tool in (SearchCodeTool(), ReadFileTool(), FileInfoTool(), ListFiltersTool()):
        registry.register(tool)
    return registry


__all__ = [
    "Tool", "ToolContext", "ToolExecution", "ToolLimits", "ToolOutcome",
    "ToolRegistry", "build_default_registry",
]
