    }


# ---------------------------------------------------------------------------
# Managed 9Router deployment
# ---------------------------------------------------------------------------

def _suggested_gui_domain() -> str:
    """A distinct subdomain on the same zone, offered as a one-click fix."""
    from syte.caddy_routes import NINE_ROUTER_GUI_HOST

    return NINE_ROUTER_GUI_HOST


async def _router_gui_guard() -> dict[str, Any] | None:
    """Require a separate Syte origin before handing api.sycord.site to 9Router.

    ``gui_domain`` defaults to ``api.sycord.site`` for LiteLLM/Syra setups
    (``host_setup.prepare_syra_host``), which is the same host the managed
    Router needs to take over. Without this guard the operator would silently
    lose the Syte console; the response instead carries enough information
    (``gui_domain_conflict`` + ``suggested_gui_domain``) for the Router tab to
    offer a one-click fix rather than sending the operator to hunt through
    Settings for the cause.
    """
    from syte.nine_router_manager import router_status

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    if gui_domain and gui_domain != NINE_ROUTER_PUBLIC_HOST:
        return None
    status = await router_status()
    return {
        **status,
        "ok": False,
        "gui_domain_conflict": True,
        "suggested_gui_domain": _suggested_gui_domain(),
        "message": (
            "Configure a separate GUI domain in Settings before deploying 9Router. "
            f"The managed Router takes over https://{NINE_ROUTER_PUBLIC_HOST}/. "
            f"Use https://{_suggested_gui_domain()}/ for the 9Router dashboard."
        ),
    }


async def _set_router_public_state(enabled: bool, *, force: bool = False) -> tuple[bool, str]:
    """Apply the Caddy route and roll the setting back if application fails."""
    from syte.nine_router_manager import NINE_ROUTER_ENABLED_SETTING

    previous = (await get_setting(NINE_ROUTER_ENABLED_SETTING, "0")).strip() == "1"
    if previous == enabled and not force:
        return True, "Router public route already has the requested state."
    await set_setting(NINE_ROUTER_ENABLED_SETTING, "1" if enabled else "0")
    ok, message = await apply_proxy_config()
    if ok:
        return True, message

    await set_setting(NINE_ROUTER_ENABLED_SETTING, "1" if previous else "0")
    rollback_ok, rollback_message = await apply_proxy_config()
    if rollback_ok:
        return False, f"{message}; previous Router route state was restored."
    return False, f"{message}; route-state rollback also failed: {rollback_message}"


async def _router_start() -> dict[str, Any]:
    from syte.nine_router_manager import router_status, start_router, stop_router

    async with _ROUTER_START_LOCK:
        guard = await _router_gui_guard()
        if guard:
            return guard
        before = await router_status()
        result = await start_router()
        if not result.get("ok"):
            # If a previously enabled container failed to start, do not leave
            # Caddy pointing at a dead upstream. Restore the fallback route.
            if before.get("enabled"):
                route_ok, route_message = await _set_router_public_state(False, force=True)
                result["proxy_configured"] = route_ok
                result["proxy_message"] = route_message
            return result

        route_ok, route_message = await _set_router_public_state(True, force=True)
        result["proxy_configured"] = route_ok
        result["proxy_message"] = route_message
        if not route_ok:
            # First restore the fallback route. Only stop a newly-created
            # container after the safe route is confirmed, otherwise a failed
            # Caddy reload could leave the enabled flag pointing at a dead
            # upstream.
            fallback_ok, fallback_message = await _set_router_public_state(False, force=True)
            result["fallback_configured"] = fallback_ok
            if not fallback_ok:
                route_message += f" Fallback route restore also failed: {fallback_message}"
            if fallback_ok and not before.get("running"):
                cleanup = await stop_router()
                if not cleanup.get("ok"):
                    route_message += f" Container cleanup also failed: {cleanup.get('message', '')}"
            result["ok"] = False
            result["message"] = f"9Router is running, but its public route failed: {route_message}"
        else:
            result["message"] = (
                f"{result.get('message', '9Router started')} "
                "api.sycord.site now serves the 9Router dashboard and /v1 API. "
                f"The dashboard is available at https://{_suggested_gui_domain()}/dashboard."
            )
        return result


async def _router_stop() -> dict[str, Any]:
    from syte.nine_router_manager import NINE_ROUTER_ENABLED_SETTING, router_status, stop_router

    async with _ROUTER_START_LOCK:
        enabled = (await get_setting(NINE_ROUTER_ENABLED_SETTING, "0")).strip() == "1"
        if enabled:
            # Move public traffic away before stopping the upstream. If this
            # fails, keep the enabled flag and live route unchanged.
            route_ok, route_message = await _set_router_public_state(False)
            if not route_ok:
                status = await router_status()
                return {
                    **status,
                    "ok": False,
                    "proxy_configured": False,
                    "proxy_message": route_message,
                    "message": f"Could not restore the LiteLLM route; 9Router remains enabled: {route_message}",
                }

        result = await stop_router()
        result["proxy_configured"] = True
        result["proxy_message"] = "LiteLLM/remote 9Router fallback route is active." if enabled else ""
        if not result.get("ok"):
            result["message"] = f"{result.get('message', 'Failed to stop 9Router')} Public fallback route is active."
        return result


async def _router_restart() -> dict[str, Any]:
    from syte.nine_router_manager import start_router, stop_router, router_status

    async with _ROUTER_START_LOCK:
        guard = await _router_gui_guard()
        if guard:
            return guard
        # Use the same safe handoff as stop/start instead of restarting the
        # container while Caddy still points at an unavailable upstream.
        enabled = (await get_setting("nine_router_public_enabled", "0")).strip() == "1"
        if enabled:
            route_ok, route_message = await _set_router_public_state(False)
            if not route_ok:
                return {**await router_status(), "ok": False, "message": route_message}
        stopped = await stop_router()
        if not stopped.get("ok"):
            return stopped
        started = await start_router()
        if not started.get("ok"):
            return started
        route_ok, route_message = await _set_router_public_state(True, force=True)
        started["proxy_configured"] = route_ok
        started["proxy_message"] = route_message
        if not route_ok:
            cleanup = await stop_router()
            if not cleanup.get("ok"):
                route_message += f" Container cleanup also failed: {cleanup.get('message', '')}"
            started["ok"] = False
            started["message"] = f"9Router restarted, but its public route failed: {route_message}"
        return started


@app.get("/api/settings/router/status")
async def api_router_status(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Return status for the managed local 9Router deployment."""
    from syte.nine_router_manager import router_status

    result = await router_status()
    result["syte_gui_url"] = await _gui_url()
    if result.get("enabled"):
        result["warning"] = (
            "api.sycord.site is currently owned by 9Router. "
            "The Syte console is available at the configured separate GUI domain."
        )
    else:
        result["warning"] = ""
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    result["gui_domain_conflict"] = not gui_domain or gui_domain == NINE_ROUTER_PUBLIC_HOST
    if result["gui_domain_conflict"]:
        result["suggested_gui_domain"] = _suggested_gui_domain()
        if not result.get("enabled"):
            result["warning"] = (
                f"Set a separate GUI domain (e.g. {result['suggested_gui_domain']}) in Settings "
                "before starting 9Router — it will otherwise be blocked."
            )
    return result
