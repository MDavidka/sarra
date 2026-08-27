"""Syte AI Builder & Agent Subsystem (OpenCode-inspired autonomous engine)."""

from syte.ai.engine import AIAgentEngine
from syte.ai.providers import UnifiedAIClient
from syte.ai.router import router as ai_router

__all__ = ["AIAgentEngine", "UnifiedAIClient", "ai_router"]
