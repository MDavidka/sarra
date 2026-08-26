# Control Plane reference-led change set

## Scope

This change is limited to the internal `control-plane-nextjs` Share It template. It changes only the **landing**, **login**, and authenticated **Console home** presentation. It does not alter the Share It catalogue, project provisioning, API routes, scoped authorization, terminal/file/startup/network/access behavior, or surrounding Syte interface.

## Mobile-first contract

The landing page must flow in this order on narrow screens: compact brand row, headline and concise subcopy, one primary workspace action, then a short product/status surface. The login page must flow: light status/brand scene, single-column password form, existing notice handling, and back action. The console home retains its mobile bottom navigation and keeps lifecycle actions visible before supporting cards.

## Reference-derived applications

| Screen | Applied reference pattern | Preserved behavior |
|---|---|---|
| Landing | The verified cloud-server page’s centered editorial headline, sparse header, near-black primary action, and one wide product montage. The montage is recreated as a Control Plane status surface using existing project concepts, not copied artwork. | Existing sign-in and Open workspace behavior. |
| Login | The verified login page’s off-white field, rounded white desktop panel, quiet monochrome supporting scene, near-black submit control, and narrow form column. | Existing password-only authentication, errors, loading state, and landing return. |
| Console home | A conservative hierarchy pass only, because the supplied hosting-platform home shot was inaccessible. The existing status, lifecycle controls, metrics, and project details remain in place. | Existing start, stop, deploy, refresh, and all scoped data. |

## Explicit non-goals

No social providers, no new claims or routes, no copied logos or reference artwork, no added dashboard widgets, no new navigation, no change to interaction semantics, and no redesign of Terminal, Files, Startup, Network, or Access.
