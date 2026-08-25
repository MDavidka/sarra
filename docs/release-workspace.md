# Release Workspace

The **Release** tab is the project-level operational control plane for a legacy Syte project. It adds deployment environments, protected promotion, preview lifecycle controls, resource governance, data recovery snapshots, project roles, and an audit timeline without replacing the existing project runtime or deployment engine.

## Runtime model

The current project remains the runtime source of truth. Production releases queue the existing recorded deployment engine, so build output, health checks, deploy history, log streaming, and rollback behavior remain unchanged. The Release workspace records policy and orchestration state around that engine.

| Control | Behavior |
|---|---|
| Production | Uses the project’s configured branch and queues the existing deploy pipeline. Production is protected by default. |
| Staging | Stores its own branch, domain, automation, and protection policy. A release is only queued when its branch matches the project branch, preventing an accidental branch switch. |
| Preview | Starts or stops the existing isolated preview runtime. Preview may be disabled per project policy. |
| Strategy | Records rolling, blue-green, or canary intent for the release record. The current single-host runtime still uses the established deploy engine, which keeps production behavior stable. |

## Protection and access

A protected environment creates a pending approval when an operator requests a release. Only a project owner or administrator may approve or reject it. Approved requests are single-use and are consumed only after the release is successfully queued.

Project roles are **owner**, **admin**, **deployer**, and **viewer**. Once project membership is configured, named account sessions without a matching membership are view-only. Bootstrap or API-token operations retain their existing administrative behavior so existing automation is not interrupted.

## Recovery and backups

The **Record point** action creates a compressed local snapshot of the project’s persistent `data/` directory under the Syte data directory. Source-code recovery remains handled by the existing deployment-run rollback path, which uses recorded Git commits. Verifying a recovery point checks that its local artifact remains available and records the check in the release timeline.

> Recovery snapshots contain persistent application data. Treat server-level Syte data backups as sensitive operational artifacts and keep filesystem access restricted to authorized operators.

## Observability and governance

The workspace exposes the current host CPU, memory, and disk pressure beside each project’s configurable alert threshold. Failed project health checks are written to the release timeline in addition to the existing notification flow. Project CPU and memory limits continue to be configured through the existing **Speed** workspace and take effect on the next Docker deployment.

## Validation

Run these checks before publishing changes to release controls:

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. python3 scripts/check_application_import.py
PYTHONPATH=. python3 scripts/check_code_health.py
node --check syte/static/app.js
git diff --check
```
