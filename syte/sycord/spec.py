"""Machine-readable Sycord API specification."""

from syte import __version__
from syte.sycord.scaffold import STACKS


def build_sycord_spec(base_url: str = "") -> dict:
    base = base_url.rstrip("/")
    prefix = f"{base}/sycord/api" if base else "/sycord/api"
    return {
        "name": "Sycord Deployer API",
        "version": __version__,
        "description": (
            "Connect Sycord websites and external projects to Syte for deployment. "
            "Auto-creates workspace and {project}.sycord.site subdomain on project_connect."
        ),
        "base_url": prefix,
        "documentation": f"{prefix}/" if base else "/sycord/api/",
        "authentication": {
            "type": "api_key",
            "header": "X-API-Key",
            "alternative": "Authorization: Bearer <token>",
            "create_token": "POST /api/tokens — Syte GUI or API",
        },
        "stacks": list(STACKS),
        "workflow": [
            "1. POST /sycord/api/project_connect {name, stack} → uuid + https://{slug}.sycord.site",
            "2. POST /sycord/api/upload — add or update files",
            "3. POST /sycord/api/issue_deployment {uuid} — docker build + deploy",
            "4. GET /sycord/api/container_get?uuid= — verify container running",
            "5. POST /sycord/api/domain {uuid, domain} — optional custom domain",
        ],
        "endpoints": [
            {
                "method": "POST",
                "path": f"{prefix}/project_connect",
                "body": {
                    "name": "str (required)",
                    "stack": "nextjs | python | javascript | html5 (default nextjs)",
                    "uuid": "optional custom id",
                    "env_vars": {},
                },
                "description": "Auto workspace + subdomain {slug}.sycord.site + scaffold",
            },
            {
                "method": "GET",
                "path": f"{prefix}/container_get?uuid=",
                "description": "Container name, running state, image, url",
            },
            {
                "method": "POST",
                "path": f"{prefix}/upload",
                "body": "multipart: uuid, path, file",
            },
            {
                "method": "POST",
                "path": f"{prefix}/domain",
                "body": {"uuid": "str", "domain": "hostname"},
            },
            {
                "method": "POST",
                "path": f"{prefix}/issue_deployment",
                "body": {"uuid": "str"},
            },
            {
                "method": "GET",
                "path": f"{prefix}/spec.json",
                "auth": False,
            },
        ],
    }
