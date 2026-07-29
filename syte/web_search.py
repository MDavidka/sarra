"""Best-effort web search for agent turns (DuckDuckGo Instant Answer + optional APIs)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

import httpx

from syte.database import get_setting

logger = logging.getLogger(__name__)


async def _tavily_search(query: str, *, api_key: str, max_results: int = 5) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "include_answer": True,
            },
        )
        response.raise_for_status()
        data = response.json()
    results = [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "snippet": item.get("content") or item.get("snippet") or "",
        }
        for item in (data.get("results") or [])[:max_results]
    ]
    return {
        "ok": True,
        "provider": "tavily",
        "query": query,
        "answer": data.get("answer") or "",
        "results": results,
    }


async def _brave_search(query: str, *, api_key: str, max_results: int = 5) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
        )
        response.raise_for_status()
        data = response.json()
    web = ((data.get("web") or {}).get("results") or [])[:max_results]
    results = [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "snippet": item.get("description") or "",
        }
        for item in web
    ]
    return {"ok": True, "provider": "brave", "query": query, "answer": "", "results": results}


async def _duckduckgo_search(query: str, *, max_results: int = 5) -> dict[str, Any]:
    """Free Instant Answer API — no key required (limited depth)."""
    url = (
        "https://api.duckduckgo.com/"
        f"?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
    )
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "Syte-Agent/1.0"})
        response.raise_for_status()
        data = response.json()

    results: list[dict[str, str]] = []
    abstract = str(data.get("AbstractText") or "").strip()
    abstract_url = str(data.get("AbstractURL") or "").strip()
    heading = str(data.get("Heading") or "").strip()
    if abstract:
        results.append({
            "title": heading or "Abstract",
            "url": abstract_url,
            "snippet": abstract,
        })
    for topic in (data.get("RelatedTopics") or [])[: max_results * 2]:
        if not isinstance(topic, dict):
            continue
        if "Topics" in topic:
            for nested in topic.get("Topics") or []:
                if isinstance(nested, dict) and nested.get("Text"):
                    results.append({
                        "title": str(nested.get("Text") or "")[:120],
                        "url": str(nested.get("FirstURL") or ""),
                        "snippet": str(nested.get("Text") or ""),
                    })
        elif topic.get("Text"):
            results.append({
                "title": str(topic.get("Text") or "")[:120],
                "url": str(topic.get("FirstURL") or ""),
                "snippet": str(topic.get("Text") or ""),
            })
        if len(results) >= max_results:
            break
    return {
        "ok": True,
        "provider": "duckduckgo",
        "query": query,
        "answer": abstract,
        "results": results[:max_results],
    }


async def web_search(query: str, *, max_results: int = 5) -> dict[str, Any]:
    """Search the web using the best configured provider.

    Preference order: Tavily → Brave → DuckDuckGo Instant Answer.
    API keys are read from system settings ``tavily_api_key`` / ``brave_api_key``.
    """
    clean = (query or "").strip()
    if not clean:
        return {"ok": False, "error": "empty_query", "message": "Provide a search query."}
    max_results = max(1, min(int(max_results or 5), 10))

    tavily_key = (await get_setting("tavily_api_key") or "").strip()
    if tavily_key:
        try:
            return await _tavily_search(clean, api_key=tavily_key, max_results=max_results)
        except Exception as exc:
            logger.info("tavily search failed, falling back: %s", exc)

    brave_key = (await get_setting("brave_api_key") or "").strip()
    if brave_key:
        try:
            return await _brave_search(clean, api_key=brave_key, max_results=max_results)
        except Exception as exc:
            logger.info("brave search failed, falling back: %s", exc)

    try:
        return await _duckduckgo_search(clean, max_results=max_results)
    except Exception as exc:
        return {
            "ok": False,
            "error": "search_failed",
            "message": str(exc) or type(exc).__name__,
            "query": clean,
        }
