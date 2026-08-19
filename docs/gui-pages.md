# SARRA operator GUI pages

The GUI sidebar is implemented as one deep-linkable URL per item. The shell routes browser history to page modules and uses `/api/platform/navigation/{page}` plus existing Syte APIs (`/api/operator/session`, project deployment/log/runtime APIs, Caddy/proxy, certificates, GitHub, model/agent, backups, and platform store records) where available. Thin platform navigation actions persist operator intent as audit events when a specialized backend endpoint does not yet exist.

| Page | Route | Visual idea | APIs used |
| --- | --- | --- | --- |
| Home | `/` or `/home` | KPI strip + activity feed | `/api/platform/navigation/home`, `/api/platform/overview/metrics`, `/api/projects`, system stats/resource monitor |
| Projects | `/projects` | Data table + right inspector | `/api/projects`, `/api/platform/navigation/projects`, deployment/project APIs |
| Overview | `/projects/:id/overview` | Status header + action bar | `/api/projects/{id}`, logs, deploy, domain, health, deployment config APIs |
| Schedules | `/projects/:id/schedules` | Cron rows + next-run rail | `/api/platform/navigation/schedules`, backup scheduler/store APIs |
| Traefik File System | `/projects/:id/proxy` | Full-height code editor | `/api/platform/navigation/traefik`, Caddy route/certificate/proxy reload APIs |
| Docker | `/projects/:id/docker` | Dense ops table + log drawer | `/api/platform/navigation/docker`, docker deploy/runtime/store APIs |
| Settings | `/settings` | Vertical form sections | `/api/settings`, `/api/settings/*`, backup manager, self-update |
| Profile | `/profile` | Identity card | `/api/platform/navigation/profile`, operator token/session APIs |
| Sessions | `/session` | Timeline / session list | `/api/operator/session`, CSRF-protected `DELETE /api/operator/session`, audit/session records |
| Remote Servers | `/servers` | Fleet list + health dots | `/api/platform/navigation/remote-servers`, host setup and platform server records |
| Users | `/users` | Directory table | `/api/tokens`, `/api/platform/navigation/users`, audit records |
| Audit Logs | `/audit` | Log viewer | `/api/platform/navigation/audit-logs`, platform webhook/audit events |
| SSH Keys | `/ssh-keys` | Keyring list | `/api/platform/navigation/ssh-keys`, `/api/platform/ssh-keys/generate` |
| AI | `/ai` | Chat + tool inspector | agent session/activity APIs, model catalog, MCP, skills, metrics, Turso |
| Tags | `/tags` | Chip manager | `/api/platform/navigation/tags` |
| Git | `/git` | Repo + PR list | GitHub settings/status/pulls APIs, `/api/platform/navigation/git` |
| Registry | `/registry` | Credential table | `/api/platform/navigation/registry` |
| Secrets | `/secrets` | Name-only vault | `/api/platform/navigation/secrets` with secret values redacted |
| DNS Providers | `/dns` | Zone + records | `/api/platform/navigation/dns-providers`, domain/DNS helpers |
| S3 Destinations | `/s3` | Bucket dest cards (this page only) | `/api/platform/navigation/s3-destinations`, backup manager/store APIs |
| Certificates | `/certificates` | Expiry dashboard | certificate, SSL resolve/debug/status, Caddy/proxy APIs |
| Notifications | `/notifications` | Channel + delivery log | `/api/platform/navigation/notifications`, webhook events |
| Billing | `/billing` | Ledger | `/api/platform/navigation/billing`, agent metrics, usage events |
| License | `/license` | Certificate-like panel | `/api/platform/navigation/license`, version/feature records |
| SSO | `/sso` | Provider forms | `/api/platform/navigation/sso` |
| Documentation | `/docs` | Reader | README/docs files, `/api/`, api-docs.html, agent streaming docs |
| Support | `/support` | Incident toolbox | `/api/platform/navigation/support`, diagnostics, audit, system status |
