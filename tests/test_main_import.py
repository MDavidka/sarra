"""Regression coverage for application-module start-up."""

from importlib import import_module


def test_main_module_imports_after_agent_feature_removal():
    """The server must not import modules removed with the optional agent feature."""
    module = import_module("syte.main")

    assert module.app is not None
