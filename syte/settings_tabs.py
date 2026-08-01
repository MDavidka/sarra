"""Registry for client-extensible settings tabs.

Clients can register new tabs in the Settings page via
``register_settings_tab``. Each tab is rendered as a card in the
settings stack and may contain arbitrary HTML content plus a
optional ``on_mount`` JavaScript callback.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

_tab_registry: List[Dict[str, Any]] = []


def register_settings_tab(
    *,
    id: str,
    name: str,
    icon: str,
    content_html: str,
    on_mount_js: str | None = None,
) -> None:
    """Register a new settings tab.

    Parameters
    ----------
    id:
        Unique identifier for the tab.
    name:
        Display name shown in the tab header.
    icon:
        Lucide icon name for the tab.
    content_html:
        HTML string rendered inside the tab card.
    on_mount_js:
        Optional JavaScript snippet executed when the tab becomes
        visible.  The function receives no arguments and ``this``
        refers to the tab card element.
    """
    _tab_registry.append(
        {
            "id": id,
            "name": name,
            "icon": icon,
            "content_html": content_html,
            "on_mount_js": on_mount_js,
        }
    )


def get_registered_tabs() -> List[Dict[str, Any]]:
    """Return a shallow copy of all registered settings tabs."""
    return list(_tab_registry)


def clear_registered_tabs() -> None:
    """Remove all registered tabs (useful in tests)."""
    _tab_registry.clear()