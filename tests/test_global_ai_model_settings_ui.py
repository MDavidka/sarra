from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_global_ai_workspace_includes_accessible_model_settings_tab() -> None:
    html = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")

    assert 'id="global-ai-tab-models"' in html
    assert 'aria-controls="global-ai-panel-models"' in html
    assert 'id="global-ai-panel-models"' in html
    assert 'id="global-ai-session-model"' in html
    assert 'id="global-ai-default-model"' in html
    assert 'id="global-ai-save-default-model"' in html
    assert 'id="global-ai-provider-list"' in html
    assert 'data-provider-type="openai"' in html
    assert 'data-provider-type="anthropic"' in html
    assert 'id="global-ai-save-provider"' in html
    assert 'ai-settings-sheet' not in html
    assert 'ai-header-settings-btn' not in html
    assert 'global-ai-provider-settings' not in html
    assert 'global-ai-open-provider-settings' not in html


def test_global_ai_model_settings_are_bound_to_existing_model_and_settings_apis() -> None:
    script = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")

    assert "function setGlobalAiTab(tab)" in script
    assert "function syncGlobalAiModelSelection" in script
    assert "function saveGlobalAiDefaultModel" in script
    assert "function saveGlobalAiProvider" in script
    assert "function loadGlobalAiProviderCatalog" in script
    assert "provider_type: globalAiProviderType" in script
    assert "agent_default_model_profile: profile" in script
    assert "document.getElementById('global-ai-session-model')?.addEventListener('change'" in script
    assert "document.getElementById('global-ai-save-default-model')?.addEventListener('click', saveGlobalAiDefaultModel)" in script
    assert "option.textContent = `${provider} · ${model.name}`" in script
    assert "provider: 'Google Gemini', name: 'Gemini 2.5 Flash'" in script
    assert "showView('ai');" in script
    assert "setGlobalAiTab('models');" in script
    assert "settingsButton.textContent = 'Models & providers';" in script


def test_global_ai_workspace_has_compact_responsive_styles() -> None:
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    assert ".global-ai-tabs" in css
    assert ".global-ai-models-panel" in css
    assert ".global-ai-model-grid" in css
    assert ".global-ai-provider-types" in css
    assert ".global-ai-provider-card" in css
    assert ".global-ai-chat-host #svc-panel-debug-chat" in css
