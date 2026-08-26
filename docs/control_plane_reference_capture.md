# Control Plane reference capture

## 2026-08-26

The supplied Dribbble hosting-platform-home URL did not render visual content in the browser after an initial navigation and one wait attempt. No layout pattern was inferred from the blank response. A visual search fallback is required before applying any home-screen styling.

## Landing reference: Cloud Server

Source: https://dribbble.com/shots/24759250-Landing-page-for-Cloud-Server

The rendered Dribbble page exposes the original shot image at `https://cdn.dribbble.com/userupload/16290313/file/original-9110985eb577a9777e72266b519f7b71.png?resize=752x&vertical=center`. Its indexed preview shows a dark cloud-server hero composition, a compact top navigation, strong centered headline, restrained primary CTA, and secondary product cards. These patterns are candidates only for the Control Plane landing screen; existing functionality and copy remain preserved.

## Login reference: AI Website

Source: https://dribbble.com/shots/27139066-Login-Signup-Page-AI-Website

The Dribbble page identifies a reference palette of `#F3F3F5`, `#BABABA`, `#090909`, `#5C5C5D`, `#ACCAF1`, and `#3960D7`. The screenshot itself did not upload through the browser, so no unverified geometry is inferred until its original shot image can be inspected.

## Verified visual mapping

| Target screen | Verified source patterns to adopt | Explicitly excluded changes |
|---|---|---|
| Landing | White canvas; compact header; large centered serif-like headline; concise subcopy; dark primary CTA with a secondary text CTA; a wide below-fold product visual; muted customer/trust row. | No external brand marks, no copied artwork or people, no change to authentication or hosting behavior. |
| Login | Light neutral background; a rounded white split panel on desktop; soft monochrome visual/supporting panel; quiet right-side form hierarchy; near-black primary action; restrained blue accent; fields and actions stay single-column on mobile. | No social-login claims, no invented providers, no change to password/session handling. |
| Home | The supplied Dribbble page remained inaccessible and did not expose a shot image through its saved HTML. Only conservative hosting-dashboard principles will be applied: existing console information stays intact; hierarchy and spacing may be refined without adding new visual systems or speculative widgets. | No attempt to recreate an unseen screen; no functional or navigation change. |

The Cloud Server original image was visually inspected directly. It uses a centered, editorial headline on white, paired dark and outline actions, and one wide product montage directly below. The Login thumbnail was also visually inspected: a restrained off-white background, rounded white main panel, soft monochrome left illustration/quote area, and focused form on the right.

## Local desktop verification

The local production build was opened at desktop width. The landing renders a compact brand/header, large editorial headline, dark primary action, and a single dark Control Plane preview surface; its preview uses only current product concepts. The login renders a neutral split panel with a monochrome supporting visual and a focused password form. The sign-in input, submit action, notice area, and return-to-landing action remained present and interactive. No credential was submitted during this review.

At 390×844, the landing stacked the copy, actions, security note, and product preview without overflow. The login retained its full password workflow and contained its form correctly, but its decorative status-card visual was too close to the supporting editorial copy. The subsequent focused CSS correction reduces that decorative visual only on narrow screens; desktop composition and all login behavior are retained. A second 390×844 capture confirmed the status visual now sits above the supporting copy with clear separation and no form overflow.
