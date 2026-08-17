"""Git repository identity and inbound webhook verification.

Pure helpers, no I/O. Two jobs:

1. **Repository identity.** An operator pastes a repo URL in whatever form they
   have it — HTTPS, SSH, ``git@``, with or without ``.git``, with or without a
   trailing slash, occasionally with credentials embedded. A push webhook then
   arrives naming the repository in the provider's own canonical form. Matching
   those reliably means normalising both sides rather than trying to make one
   ``LIKE`` pattern cover every case, which is why
   :func:`syte.platform.store.applications_watching` filters in Python.

2. **Webhook authenticity.** Each provider signs deliveries differently.
   Verification is centralised here so the HTTP layer cannot accidentally skip
   it, and every comparison uses :func:`hmac.compare_digest`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import re
from urllib.parse import urlsplit

from syte.platform.types import GitProvider

# `git@github.com:owner/repo.git` is not a URL and urlsplit cannot parse it, so
# the scp-like form gets its own pattern.
_SCP_LIKE_RE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")

# Hosts we can classify without the operator telling us.
_PROVIDER_HOSTS: tuple[tuple[str, GitProvider], ...] = (
    ("github.com", GitProvider.GITHUB),
    ("gitlab.com", GitProvider.GITLAB),
    ("bitbucket.org", GitProvider.BITBUCKET),
    ("gitea.com", GitProvider.GITEA),
    ("codeberg.org", GitProvider.GITEA),
)


def normalize_repo(value: str) -> str:
    """Reduce any repository reference to ``host/owner/name``, lowercased.

    Returns an empty string when nothing usable can be extracted, so callers can
    treat "unparseable" and "no match" identically.

    >>> normalize_repo("git@github.com:Acme/Web.git")
    'github.com/acme/web'
    >>> normalize_repo("https://github.com/acme/web/")
    'github.com/acme/web'
    >>> normalize_repo("acme/web")
    'acme/web'
    """
    raw = (value or "").strip()
    if not raw:
        return ""

    host = ""
    path = raw

    if "://" in raw:
        parts = urlsplit(raw)
        # urlsplit puts any embedded credentials in netloc; hostname drops them.
        host = (parts.hostname or "").lower()
        path = parts.path
    else:
        match = _SCP_LIKE_RE.match(raw)
        if match and "/" in match.group("path"):
            host = match.group("host").lower()
            path = match.group("path")

    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[: -len(".git")]
    path = path.strip("/").lower()

    # A repository is always at least ``owner/name``. Requiring two path
    # segments is what rejects junk that happens to survive parsing — without
    # it, an unparseable string like ``git@github.com:`` would be returned
    # verbatim and could then "match" itself.
    if len([segment for segment in path.split("/") if segment]) < 2:
        return ""
    return f"{host}/{path}" if host else path


def repo_matches(stored: str, incoming: str) -> bool:
    """True when two repository references identify the same repository.

    Host-qualified references must agree on the host — otherwise a push to
    ``gitlab.com/acme/web`` would deploy an application tracking
    ``github.com/acme/web``. When one side carries no host (an operator wrote
    just ``acme/web``) the owner/name pair is compared instead.
    """
    left = normalize_repo(stored)
    right = normalize_repo(incoming)
    if not left or not right:
        return False
    if left == right:
        return True

    left_parts = left.split("/")
    right_parts = right.split("/")
    left_has_host = len(left_parts) > 2
    right_has_host = len(right_parts) > 2
    if left_has_host and right_has_host:
        return False

    # Compare the trailing owner/name pair when at least one side is host-less.
    return left_parts[-2:] == right_parts[-2:]


def provider_for_repo(value: str) -> GitProvider:
    """Best-effort provider classification from a repository reference."""
    normalized = normalize_repo(value)
    for host, provider in _PROVIDER_HOSTS:
        if normalized.startswith(f"{host}/"):
            return provider
    return GitProvider.OTHER


def branch_matches(configured: str, incoming: str) -> bool:
    """Compare branch names, tolerating a full ``refs/heads/...`` ref.

    Push payloads carry ``refs/heads/main`` while the application stores
    ``main``; a plain equality check would never fire.
    """
    return _strip_ref(configured) == _strip_ref(incoming) and bool(_strip_ref(incoming))


def _strip_ref(value: str) -> str:
    ref = (value or "").strip()
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def watch_paths_match(patterns: str, changed_files: tuple[str, ...]) -> bool:
    """Whether a push touched any path an application watches.

    ``patterns`` is the newline/comma separated ``watch_paths`` column. An empty
    value means "watch everything" — the common case, and the one where a
    monorepo filter must not accidentally suppress deployments.

    A push with no reported file list also returns ``True``: failing open is
    correct here, because suppressing a real deployment is worse than running a
    redundant one.
    """
    globs = [p.strip() for chunk in (patterns or "").replace(",", "\n").splitlines() for p in [chunk] if p.strip()]
    if not globs:
        return True
    if not changed_files:
        return True
    for path in changed_files:
        normalized = path.strip().lstrip("./")
        for pattern in globs:
            candidate = pattern.lstrip("./")
            if fnmatch.fnmatch(normalized, candidate):
                return True
            # A bare directory pattern should match everything beneath it, which
            # is what an operator means by `apps/web`.
            if fnmatch.fnmatch(normalized, candidate.rstrip("/") + "/*"):
                return True
    return False


# --------------------------------------------------------------------------- #
# Webhook signature verification
# --------------------------------------------------------------------------- #


def verify_github_signature(secret: str, body: bytes, header: str) -> bool:
    """Verify a GitHub ``X-Hub-Signature-256`` header."""
    if not secret or not header:
        return False
    algorithm, _, digest = header.partition("=")
    if algorithm != "sha256" or not digest:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest.strip())


def verify_gitlab_token(secret: str, header: str) -> bool:
    """Verify a GitLab ``X-Gitlab-Token`` header.

    GitLab sends the shared secret verbatim rather than an HMAC, so this is a
    constant-time equality check — still via ``compare_digest`` to avoid leaking
    the secret's length through timing.
    """
    if not secret or not header:
        return False
    return hmac.compare_digest(secret, header.strip())


def verify_gitea_signature(secret: str, body: bytes, header: str) -> bool:
    """Verify a Gitea ``X-Gitea-Signature`` header (bare sha256 HMAC hex)."""
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def verify_webhook(
    provider: GitProvider | str,
    secret: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[bool, str]:
    """Dispatch to the right verifier. Returns ``(ok, human message)``.

    Header lookup is case-insensitive because ASGI servers and proxies disagree
    about header casing.
    """
    if isinstance(provider, str):
        try:
            provider = GitProvider(provider)
        except ValueError:
            provider = GitProvider.OTHER

    lowered = {key.lower(): value for key, value in headers.items()}

    if not secret:
        return False, (
            "No webhook secret configured for this source. Generate one in the "
            "resource's Webhooks tab and paste it into the git provider."
        )

    if provider is GitProvider.GITHUB:
        ok = verify_github_signature(secret, body, lowered.get("x-hub-signature-256", ""))
        return ok, "Signature verified." if ok else "Invalid X-Hub-Signature-256."
    if provider is GitProvider.GITLAB:
        ok = verify_gitlab_token(secret, lowered.get("x-gitlab-token", ""))
        return ok, "Token verified." if ok else "Invalid X-Gitlab-Token."
    if provider is GitProvider.GITEA:
        ok = verify_gitea_signature(secret, body, lowered.get("x-gitea-signature", ""))
        return ok, "Signature verified." if ok else "Invalid X-Gitea-Signature."
    if provider is GitProvider.BITBUCKET:
        # Bitbucket Cloud does not sign payloads. The secret is carried in the
        # URL instead, so the HTTP layer compares it before reaching here.
        return True, "Bitbucket does not sign payloads; URL secret is authoritative."

    # Unknown provider: accept a GitHub-style signature if one is present, so a
    # Forgejo/Gogs instance still works.
    ok = verify_github_signature(secret, body, lowered.get("x-hub-signature-256", ""))
    return ok, "Signature verified." if ok else "No recognised signature header."


__all__ = [
    "branch_matches",
    "normalize_repo",
    "provider_for_repo",
    "repo_matches",
    "verify_gitea_signature",
    "verify_github_signature",
    "verify_gitlab_token",
    "verify_webhook",
    "watch_paths_match",
]
