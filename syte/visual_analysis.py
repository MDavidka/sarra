"""Screenshot → structured visual analysis for Syra design feedback loops.

Produces layout / color / typography / issues / suggestions sections suitable
for injecting into the agent system prompt (Improve UI from screenshot).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

VISUAL_ANALYSIS_SYSTEM = """You are a senior product designer reviewing a website screenshot.
Describe all UI sections, exact layout, color tokens, typography, spacing, and any visual issues.
Output ONLY valid JSON with these keys:
{
  "description": "overall detailed description",
  "layout": "sections and hierarchy",
  "color_scheme": "palette / tokens observed",
  "typography": "font stacks, sizes, weights",
  "components": "buttons, cards, nav, forms, etc.",
  "accessibility": "contrast, tap targets, labels",
  "performance": "heavy imagery, layout shift risks",
  "issues": ["..."],
  "suggestions": ["..."],
  "mobile_tweaks": ["..."]
}
Be specific and actionable. Prefer minimal structural changes."""

BLUEPRINT_SYSTEM = """You convert a UI screenshot description into a design blueprint for rebuilding
a similar site (layout patterns only — not copy). Output ONLY valid JSON:
{
  "sections": ["hero", "features", "..."],
  "grid": "description of columns / max-width / spacing rhythm",
  "colors": {"primary": "", "background": "", "accent": "", "notes": ""},
  "typography": {"display": "", "body": "", "scale_notes": ""},
  "components": ["..."],
  "accessibility": ["..."],
  "performance": ["..."],
  "mobile": "mobile layout notes"
}"""


def parse_analysis_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from a model reply (tolerates fences / preamble)."""
    raw = (text or "").strip()
    if not raw:
        return {}
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def normalize_analysis(data: dict[str, Any]) -> dict[str, Any]:
    def _str(key: str) -> str:
        val = data.get(key)
        if isinstance(val, list):
            return "\n".join(str(x) for x in val)
        return str(val or "").strip()

    def _list(key: str) -> list[str]:
        val = data.get(key)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        if isinstance(val, str) and val.strip():
            return [line.strip("- ").strip() for line in val.splitlines() if line.strip()]
        return []

    return {
        "description": _str("description"),
        "layout": _str("layout"),
        "color_scheme": _str("color_scheme") or _str("colorScheme"),
        "typography": _str("typography"),
        "components": _str("components"),
        "accessibility": _str("accessibility"),
        "performance": _str("performance"),
        "issues": _list("issues"),
        "suggestions": _list("suggestions") or _list("suggested_improvements"),
        "mobile_tweaks": _list("mobile_tweaks") or _list("mobile"),
    }


def heuristic_analysis_from_meta(
    *,
    viewport: str,
    width: int,
    height: int,
    route: str,
) -> dict[str, Any]:
    """Offline fallback when no vision model is available."""
    issues: list[str] = []
    suggestions: list[str] = []
    if viewport == "phone" and width and width < 400:
        suggestions.append("Verify tap targets ≥ 44px and stacked CTAs on mobile.")
    if viewport == "desktop":
        suggestions.append("Check hero spacing, max-w container centering, and CTA contrast.")
        issues.append("Automated vision unavailable — review spacing and hierarchy manually.")
    return normalize_analysis({
        "description": (
            f"Screenshot of route {route} at {viewport} ({width}x{height}). "
            "Structured vision analysis was not available; use this as a placeholder "
            "and re-analyze with a vision-capable profile (syra-nano / syra-havy)."
        ),
        "layout": f"{viewport} viewport capture of {route}",
        "color_scheme": "unknown — re-run with vision model",
        "typography": "unknown — re-run with vision model",
        "components": "unknown",
        "accessibility": "Verify contrast and focus states after visual review.",
        "performance": "Prefer optimized images and avoid layout shift in hero.",
        "issues": issues,
        "suggestions": suggestions,
        "mobile_tweaks": [
            "Tighten hero padding on small screens",
            "Ensure nav collapses cleanly",
        ] if viewport == "phone" else [],
    })


async def analyze_screenshot_with_vision(
    model: dict[str, str],
    *,
    image_base64: str,
    viewport: str,
    width: int = 0,
    height: int = 0,
    route: str = "/",
) -> dict[str, Any]:
    """Call the provider with a vision image part; fall back to heuristics."""
    from syte.cloud_agent import _provider_completion

    if not image_base64 or not model.get("api_key"):
        return heuristic_analysis_from_meta(
            viewport=viewport, width=width, height=height, route=route,
        )

    # DeepSeek (syra-base) does not support vision — use heuristic unless profile claims vision.
    provider = (model.get("provider") or "").lower()
    supports_vision = "gemini" in provider or "verted" in provider or model.get("supports_vision")
    if not supports_vision and "deepseek" in provider:
        return heuristic_analysis_from_meta(
            viewport=viewport, width=width, height=height, route=route,
        )

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Analyze this {viewport} UI screenshot ({width}x{height}) of route {route}. "
                "Return the JSON object described in the system prompt."
            ),
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_base64}"},
        },
    ]
    messages = [
        {"role": "system", "content": VISUAL_ANALYSIS_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    try:
        assistant = await _provider_completion(
            model,
            messages,
            tools=[],
            temperature=0.2,
            stream=False,
        )
        parsed = parse_analysis_json(str(assistant.get("content") or ""))
        if not parsed:
            return heuristic_analysis_from_meta(
                viewport=viewport, width=width, height=height, route=route,
            )
        return normalize_analysis(parsed)
    except Exception:
        logger.exception("Vision analysis failed; using heuristic fallback")
        return heuristic_analysis_from_meta(
            viewport=viewport, width=width, height=height, route=route,
        )


async def analyze_and_store(
    project_id: str,
    *,
    screenshot_id: str,
    image_base64: str,
    viewport: str,
    width: int = 0,
    height: int = 0,
    route: str = "/",
    screenshot_url: str = "",
    session_id: str | None = None,
    session_number: int = 0,
    model: dict[str, str] | None = None,
) -> dict[str, Any]:
    from syte.agent_memory import save_visual_analysis

    analysis_data = await analyze_screenshot_with_vision(
        model or {},
        image_base64=image_base64,
        viewport=viewport,
        width=width,
        height=height,
        route=route,
    )
    return await save_visual_analysis(
        project_id,
        viewport=viewport,
        description=analysis_data.get("description") or "",
        issues=list(analysis_data.get("issues") or []),
        suggestions=list(analysis_data.get("suggestions") or []),
        screenshot_id=screenshot_id,
        screenshot_url=screenshot_url,
        session_id=session_id,
        session_number=session_number,
        layout=analysis_data.get("layout") or "",
        color_scheme=analysis_data.get("color_scheme") or "",
        typography=analysis_data.get("typography") or "",
        components=analysis_data.get("components") or "",
        accessibility=analysis_data.get("accessibility") or "",
        performance=analysis_data.get("performance") or "",
        mobile_tweaks=list(analysis_data.get("mobile_tweaks") or []),
        raw=analysis_data,
    )


def blueprint_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Turn a visual analysis into a reference blueprint for new generations."""
    return {
        "sections": [
            s.strip()
            for s in re.split(r"[,;\n]+", str(analysis.get("layout") or ""))
            if s.strip()
        ][:12]
        or ["hero", "features", "cta"],
        "grid": analysis.get("layout") or "max-w-6xl centered, generous section padding",
        "colors": {"notes": analysis.get("color_scheme") or ""},
        "typography": {"scale_notes": analysis.get("typography") or ""},
        "components": [
            s.strip()
            for s in re.split(r"[,;\n]+", str(analysis.get("components") or ""))
            if s.strip()
        ][:20],
        "accessibility": analysis.get("issues") or [],
        "performance": [analysis.get("performance") or ""] if analysis.get("performance") else [],
        "mobile": "; ".join(analysis.get("mobile_tweaks") or []) or analysis.get("accessibility") or "",
    }
