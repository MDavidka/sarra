# Project Edit reference mapping

## Scope and correction

The previous Project Edit pass used a dark color layer without reproducing the supplied screens’ information hierarchy, control placement, or mobile flow. This correction removes that layer. The revised Syte Project Edit view uses **white surfaces**, charcoal text, thin neutral outlines, compact rectangular controls, and a small number of state indicators. It does not introduce any mock analytics, fake deployment records, invented domain statuses, or visible secret values.

## Reference-to-feature mapping

| Reference screen pattern | Existing Syte feature | Revised implementation rule |
| --- | --- | --- |
| Centered compact project header with a back affordance and discrete action control | Main Project Edit top bar and existing Project sidebar back control | Use the real project breadcrumb and existing back route. On mobile, expose the current real panels in a compact horizontal rail rather than an unrelated second navigation system. |
| Narrow environment, author, date, and status controls above a deployment list | Existing General and Release deployment controls | Present only real deployment status, branch, source, health, and release information. Use grouped filter-shaped layout only for actual controls and labels. |
| Clear stacked deployment rows with a status dot, summary, commit/source metadata, and a right-side action | Existing deployment history and Release workspace | Keep the current API-powered deployment history and release actions. Improve row hierarchy and action placement; do not fabricate commits, authors, dates, previews, or environments. |
| Domain entry list with a prominent primary action and a secondary management action | Existing primary domain, custom-domain form, and certificate readiness action | Show the real production domain and domain form in a structured list treatment. Do not add domain purchasing, search, or configuration states that Syte cannot fulfill. |
| Environment Variables title, primary add action, secondary shared action, tabs, filters, and locked variable rows | Existing environment card list and add-variable dialog | Keep values redacted and retain the existing modal. Show controls and variable cards as a dense white operational list; do not claim shared-variable support unless it exists. |
| Firewall summary with control strip, system state, and policy rows | Existing project firewall policy panel | Preserve the explicit project-level policy statement and server-level boundary. Use its real policy rows and no synthetic request counts, bot protection, or query controls. |
| Deployment log header, tab-like context navigation, terminal, and summary rows | Existing deployment log stream and refresh/autoscroll controls | Keep live log semantics and use the actual toolbar, terminal, and status indicator. Do not render static log examples. |
| Analytics counters, chart, and page breakdown | No corresponding scoped Syte analytics data | Omit rather than invent analytics. |

## Mobile-first layout rules

The revised layout uses a single content column, a 44-pixel minimum target for primary touch controls, a horizontally scrollable real-panel rail, grouped command controls, and stacked operational rows on narrow screens. Desktop expands the same structures into two-column control bands or side-by-side operational details only where the existing data already supports that density.

## Non-negotiable preservation requirements

The existing authenticated API routes, project identifiers, deployment and rollback actions, health check, log stream, custom-domain form, environment-value redaction, and scoped project permissions remain unchanged. The work is limited to layout markup, class names, rendering structure, and CSS.
