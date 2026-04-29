# coding=utf-8
"""
Assistant module package.
"""

from trendradar.assistant.router import (
    ASSISTANT_GUIDELINES,
    DEFAULT_ROUTE_RULES,
    DEFAULT_SYSTEM_PROMPTS,
    route_intent,
    resolve_system_prompt,
)
from trendradar.assistant.web import run_assistant_web, start_assistant_web_background

__all__ = [
    "ASSISTANT_GUIDELINES",
    "DEFAULT_ROUTE_RULES",
    "DEFAULT_SYSTEM_PROMPTS",
    "route_intent",
    "resolve_system_prompt",
    "run_assistant_web",
    "start_assistant_web_background",
]
