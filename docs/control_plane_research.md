# Control Plane reference findings

The redesign will use established hosting-panel patterns rather than an invented dashboard aesthetic.

## Reference principles

- Pterodactyl presents isolated Docker workloads through a user-facing server-management panel. Its public documentation emphasizes isolation, resource limits, secure credentials, and lifecycle operations as core hosting-panel responsibilities. Source: https://pterodactyl.io/
- A practical hosting control surface should separate project lifecycle, runtime console/logs, startup/configuration, files/data, networking/domain, databases, schedules, and user access. These are familiar user-level responsibilities in Pterodactyl-like panels, but the Syte template must remain limited to the one project bound to its scoped instance credential.
- The visual reference is a dense but legible dark server workspace: compact top header, a primary live console/status block, segmented navigation, clearly separated operational blocks, neutral black/white/grey colors, restrained status color only when it communicates runtime state, and mobile-first single-column ordering.

## Secure interpretation for Syte

- The template must never offer unrestricted host-shell access. The terminal exposes real project-scoped status, health, and log actions; future command expansion must remain allowlisted and tied to the template project.
- All project operations remain server-side proxy calls made with the per-instance credential. Browser users receive only signed workspace sessions.
- Configuration and files must remain project-bound. No action can select another Syte project, Docker container, or host path.

## Mobile-first block plan

1. Mobile bottom navigation for Console, Files, Startup, Network, and Settings, with an overflow area for Schedules and Users.
2. First block: runtime status, primary start/stop/restart action, direct server endpoint.
3. Second block: live console/log stream with command-bar tabs for logs, health, and status.
4. Subsequent blocks: resource and deployment state, startup configuration, files/data, network/domain, schedules, users/access, and activity.
5. Desktop moves navigation to a permanent left rail and arranges blocks into a two-column grid only after the mobile layout is complete.
