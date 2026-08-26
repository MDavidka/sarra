"""Regression coverage for certificate guidance and issuance safeguards."""
from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_certificate_issue_applies_project_tls_and_returns_dns_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import main

    updates: list[dict[str, object]] = []

    async def fake_project(project_id: str):
        return {"id": project_id, "name": "web"}

    async def fake_guidance(domain: str):
        assert domain == "app.example.com"
        return {"domain": domain, "direct_to_sycord": True}

    async def fake_cloudflare_status():
        return {"token_configured": True, "caddy_plugin_installed": True}

    async def fake_update(project_id: str, values: dict[str, object]):
        assert project_id == "project-1"
        updates.append(values)
        return {"id": project_id, **values}

    async def fake_apply_proxy_config():
        return True, "Caddy reloaded"

    monkeypatch.setattr(main, "get_project", fake_project)
    monkeypatch.setattr(main, "_certificate_dns_guidance", fake_guidance)
    monkeypatch.setattr(main, "update_project", fake_update)
    monkeypatch.setattr("syte.certificates.cloudflare_tls_status", fake_cloudflare_status)
    monkeypatch.setattr("syte.certificates.apply_proxy_config", fake_apply_proxy_config)

    result = await main.api_issue_certificate(
        main.CertificateIssueRequest(project_id="project-1", domain="app.example.com"),
        _operator={"id": "operator-1"},
    )

    assert result["ok"] is True
    assert result["issued"] is True
    assert result["dns"]["direct_to_sycord"] is True
    assert updates == [{"custom_tls_domain": "app.example.com", "custom_tls_enabled": 1}]


@pytest.mark.asyncio
async def test_wildcard_certificate_requires_cloudflare_dns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import main

    async def fake_project(project_id: str):
        return {"id": project_id, "name": "web"}

    async def fake_guidance(domain: str):
        return {"domain": domain}

    async def no_cloudflare_token():
        return {"token_configured": False, "caddy_plugin_installed": False}

    monkeypatch.setattr(main, "get_project", fake_project)
    monkeypatch.setattr(main, "_certificate_dns_guidance", fake_guidance)
    monkeypatch.setattr("syte.certificates.cloudflare_tls_status", no_cloudflare_token)

    with pytest.raises(HTTPException) as error:
        await main.api_issue_certificate(
            main.CertificateIssueRequest(project_id="project-1", domain="example.com", wildcard=True),
            _operator={"id": "operator-1"},
        )

    assert error.value.status_code == 409
    assert "Cloudflare DNS token" in str(error.value.detail)
