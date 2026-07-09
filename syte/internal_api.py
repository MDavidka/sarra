"""Syte internal API for sycord.com -> Syte runtime calls."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import httpx

from syte.auth import verify_internal_service_request
from syte.continue_agent import (
    agent_local_url,
    get_agent_logs,
    get_agent_status,
    restart_agent,
    start_agent,
    stop_agent,
)
from syte.database import get_project

router = APIRouter(tags=["Syte Internal API"])


async def _require_project(project_id: str) -> dict:
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/projects/{project_id}/agent")
async def internal_agent_status(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/start")
async def internal_agent_start(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message, _meta = await start_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/stop")
async def internal_agent_stop(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message = await stop_agent(project_id)
    return {
        "ok": ok,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/restart")
async def internal_agent_restart(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message, _meta = await restart_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.get("/projects/{project_id}/agent/logs")
async def internal_agent_logs(
    project_id: str,
    lines: int = 200,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "logs": get_agent_logs(project_id, max(1, min(lines, 2000))),
    }


@router.api_route(
    "/projects/{project_id}/agent/proxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@router.api_route(
    "/projects/{project_id}/agent/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def internal_agent_proxy(
    project_id: str,
    request: Request,
    path: str = "",
    _auth: dict = Depends(verify_internal_service_request),
):
    status = await get_agent_status(project_id)
    if not status:
        raise HTTPException(404, "Project not found")
    if not status.get("agent_port"):
        raise HTTPException(503, "Continue agent has no allocated port")
    if not status.get("agent_running"):
        raise HTTPException(503, "Continue agent is not running")
    upstream = agent_local_url(status["agent_port"]).rstrip("/")
    target = upstream + ("/" + path.lstrip("/") if path else "")
    query_items = [(k, v) for k, v in request.query_params.multi_items() if k != "internal_secret"]
    if query_items:
        target += "?" + urlencode(query_items)

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "x-syra-internal-secret"}
    }
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        upstream_response = await client.request(
            request.method,
            target,
            headers=headers,
            content=body,
        )
    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in {"content-encoding", "transfer-encoding", "connection"}
    }
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
