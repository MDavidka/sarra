"""HTTP API for Syte-hosted Share It templates and generated instances."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from syte.auth import verify_operator_session_or_token
from syte.share_template_service import (
    authenticate_share_instance,
    get_share_template,
    list_share_templates,
    provision_share_template,
    run_share_instance_action,
    share_instance_overview,
    configure_share_instance_access,
    verify_share_instance_access,
    share_instance_terminal,
    rotate_share_instance_access,
)

router = APIRouter(tags=["share-it"])

class ShareProvisionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

class ShareActionRequest(BaseModel):
    action: str = Field(pattern="^(start|stop|deploy)$")


class ShareAccessPasswordRequest(BaseModel):
    password: str = Field(min_length=12, max_length=200)


class ShareTerminalRequest(BaseModel):
    command: str = Field(pattern="^(status|logs|health)$")


class ShareAccessRotateRequest(BaseModel):
    current_password: str = Field(min_length=12, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)

async def _instance(instance_id: str, x_share_instance_key: str | None = Header(default=None)) -> dict[str, Any]:
    instance = await authenticate_share_instance(instance_id, x_share_instance_key or "")
    if not instance:
        raise HTTPException(401, "Invalid or missing hosted template credential.")
    return instance

@router.get("/share/templates")
async def share_templates(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    return {"templates": await list_share_templates()}

@router.get("/share/templates/{template_id}")
async def share_template(template_id: str, _operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    template = await get_share_template(template_id)
    if not template:
        raise HTTPException(404, "Template not found.")
    template.pop("source_dir", None)
    return {"template": template}

@router.post("/share/templates/{template_id}/provision")
async def provision_template(template_id: str, body: ShareProvisionRequest, operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    try:
        result, _secret = await provision_share_template(template_id, body.name, str(operator.get("id") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # The server-only value is placed directly into the generated project environment.
    # It is intentionally never returned to the operator or browser.
    return {"ok": True, **result, "message": "Template source provisioned. Deploy the project when you are ready."}

@router.post("/share/instances/{instance_id}/access")
async def configure_instance_access(instance_id: str, body: ShareAccessPasswordRequest, _operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    try:
        await configure_share_instance_access(instance_id, body.password)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "message": "Workspace access password configured."}


@router.post("/share/instances/{instance_id}/access/login")
async def verify_instance_access(instance_id: str, body: ShareAccessPasswordRequest, _instance: dict[str, Any] = Depends(_instance)):
    if not await verify_share_instance_access(instance_id, body.password):
        raise HTTPException(401, "Invalid workspace access password.")
    return {"ok": True, "message": "Workspace access granted."}


@router.post("/share/instances/{instance_id}/access/rotate")
async def rotate_instance_access(instance_id: str, body: ShareAccessRotateRequest, _instance: dict[str, Any] = Depends(_instance)):
    if not await rotate_share_instance_access(instance_id, body.current_password, body.new_password):
        raise HTTPException(401, "Current workspace access password is invalid.")
    return {"ok": True, "message": "Workspace access password changed."}


@router.get("/share/instances/{instance_id}/overview")
async def share_overview(instance_id: str, instance: dict[str, Any] = Depends(_instance)):
    try:
        return await share_instance_overview(instance)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

@router.post("/share/instances/{instance_id}/terminal")
async def share_terminal(instance_id: str, body: ShareTerminalRequest, instance: dict[str, Any] = Depends(_instance)):
    try:
        return await share_instance_terminal(instance, body.command)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/share/instances/{instance_id}/actions")
async def share_action(instance_id: str, body: ShareActionRequest, instance: dict[str, Any] = Depends(_instance)):
    try:
        return await run_share_instance_action(instance, body.action)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
