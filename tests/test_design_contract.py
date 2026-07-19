"""Tests for the Sycord design contract themes + shadcn catalog."""

from syte.design_contract import (
    DESIGN_CONTRACT_VERSION,
    DESIGN_THEMES,
    SHADCN_COMPONENT_CATALOG,
    build_design_contract_spec,
    build_system_prompt,
    shadcn_catalog_json,
    themes_prompt_block,
)


def test_five_named_themes() -> None:
    assert set(DESIGN_THEMES) == {"minimal", "bold", "corporate", "vibrant", "dark-tech"}
    for theme in DESIGN_THEMES.values():
        assert "css_vars" in theme
        assert "--radius" in theme["css_vars"]
        assert theme["fonts"]["sans"]


def test_shadcn_catalog_has_core_imports() -> None:
    names = {item["name"] for item in SHADCN_COMPONENT_CATALOG}
    assert {"Button", "Card", "Input", "Dialog", "Tabs"} <= names
    catalog = shadcn_catalog_json()
    assert "@/components/ui/button" in catalog


def test_spec_and_system_prompt_include_themes() -> None:
    assert DESIGN_CONTRACT_VERSION.startswith("1.")
    spec = build_design_contract_spec()
    assert "themes" in spec
    assert len(spec["shadcn_components"]) >= 10
    prompt = build_system_prompt()
    assert "dark-tech" in prompt
    assert "ask_question" in themes_prompt_block() or "choice" in themes_prompt_block().lower()
    assert "Button" in prompt
