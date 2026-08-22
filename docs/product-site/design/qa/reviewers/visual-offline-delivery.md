# Independent visual and offline delivery reviewer attestation

Reviewer role: `visual-offline-delivery`
Reviewer ID: `reviewer-visual-delivery-75d6b21`
Reviewer task: `/root/auth_bridge_review`
Reviewed product baseline: `75d6b216bd2c4806ecfbe5094029a70e271e4460`
Reviewed at UTC: `2026-08-22T04:45:30Z`
Verdict: `PASS`
Finding count: `0`
Canonical content SHA256: `c9c525458221e420f71ff59ca35ff1a528af915da246c75f2e4e59ce88ece956`
Canonical hash rule: `SHA-256 of UTF-8 file bytes after replacing the Canonical content SHA256 value with 64 ASCII zeroes.`

## Input hashes

`docs/product-site/design/qa/package-manifest.sha256`: `c08a89e3ab9b899c58559082261e95d6452d0ac5743db3e6f2cfd6374153483a`
`docs/product-site/design/qa/browser-acceptance-receipt.json`: `f4c4040c736d18f1e738a532e9723a40367ae58d8c1d89ac16e697c4773919f5`
`scripts/run_offline_product_site_browser_acceptance.mjs`: `597a852fe8b70d3d2893ddad56e2ac5578bd9b3e50fe23034daa3a374d1128d8`
`docs/product-site/content/offline-product-site-copy-v1.md`: `267b39711705a3b11e2e7a6ebc4894e77ed094988573c7abf94c31de1dca8a9f`
`docs/product-site/content/USER_GUIDE.zh-CN.md`: `b1bd464882e7a0ad1b163091d39d4650f16bef9630d44d968c73aa09251cbe7d`
`docs/product-site/design/offline-product-site-visual-design-spec-v1.md`: `e564ee8bc5eb16fb2d87af4b50ef000343eb3e7d95773f61dddb90bf4d1dc7ab`
`docs/product-site/design/homepage-direction-v2-approved.png`: `0526f97df004537c3d3c758fe22127ebabe524965ba9143fe5a5523d72fb206d`
`docs/product-site/design/qa/home-1440x900.png`: `4ae4c25e2285dc74401f2900cc6256e3cf6327c4a7e913cff8f71c22246ee126`
`docs/product-site/design/qa/home-1366x768.png`: `9e7df46cafbb77df55167fe77df380106aff856d61444c3808da3870aff8820f`
`docs/product-site/design/qa/home-1280x800.png`: `57ae6362fe0cb4415b7d230d021e2add251edd9080601c6582080cfa886ba250`
`docs/product-site/design/qa/home-1024x768.png`: `8065dc821a4ef3cf9cb8a145e6bd5adbc78fed57ef4af3ee43ab27cc048c29d8`
`docs/product-site/design/qa/home-390x844.png`: `071407fa619b22be6d371f4cf8b5db257ae464ebeb65a86b74e871d9b8ab9eec`
`docs/product-site/design/qa/loop-1366x768.png`: `1f2547a0c88cf285f572f75ad0fc9e4003800e4bbec177bfb85ec901247886c4`
`docs/product-site/design/qa/expert-review-1366x768.png`: `0959de97d6b07aebb0a0b0300cb4097eca7ff8b63acf2821e5b605f131c0630f`
`docs/product-site/design/qa/platform-1366x768.png`: `d4cf65d91052a757b658bcbacf1c014a2d3afb4ed537b93baf47ce4530d92f5f`
`docs/product-site/design/qa/downloads-1366x768.png`: `0ede74ae0210d799ee7643d825d9643ee3184be7c8ab4db251a09ccfcecdd4e9`
`docs/product-site/design/qa/guide-1366x768.png`: `15886d0cdb36a6a9ab9f3f7ee5daa47c9395a606289b4b9b5fc67192f019715a`
`docs/product-site/design/qa/guide-390x844.png`: `45180646805c76f3958a653ea8d72f0bd3582e9084c4cf2333e5e8b9731032b7`

## Scope

Independent review of the exact commit's visual system, first-screen hierarchy, responsive behavior, Downloads and Guide surfaces, representative screenshot bindings, Platform disclosure correction, offline request ownership, 16-entry package manifest, and schema-3 browser receipt. The approved v2-named reference image was used only as a visual-language reference; no previous reviewer conclusion was reused, and current product identity was evaluated from the v3.0.1 implementation and evidence at this exact commit.

## Independent verification

- Confirmed `HEAD` equals the full reviewed commit and recomputed all 18 exact-commit input hashes; excluded the unrelated untracked `selected-homepage-direction.png` from review inputs.
- Confirmed the source visual specification now defines the Guide as empty/existing project × online/offline × three platforms, totaling 12 self-contained routes, matching the desktop/mobile Guide screenshots and the 24 receipt route activations.
- Inspected all 11 committed QA screenshots and matched every screenshot byte hash to the receipt, including five homepage widths, the 390 px full-page reflow, Loop, deterministic Expert, Platform, Downloads, and desktop/mobile Guide captures.
- Confirmed the current v3.0.1 screens preserve the white, cobalt-blue, restrained warm-accent language and consistent typography, spacing, dividers, card treatment, and CTA hierarchy while presenting version identity and benefit evidence without visible clipping or overlap.
- Verified the 1366 px first screens expose the product claim and the first evidence region on Home, Loop, Expert, and Platform; Downloads exposes the v3.0.1 identity and Guide entry; the 390 px Home and Guide reflow into one readable column with usable controls and no horizontal overflow.
- Verified the corrected Platform disclosure says the existing synthetic values were rebound to v3.0.1 without rerunning Provider and remain non-production statistics; the changed Platform HTML hash is present in the 16-entry manifest, the manifest is bound into the receipt, and the receipt's input commit is an ancestor with zero drift across all bound paths.
- Recomputed all 16 package entries successfully, ran the current static validator (`OFFLINE_PRODUCT_SITE_VALID`), and ran the focused v3 release contract tests (`11 passed`).
- Ran the current receipt verifier: `BROWSER_ACCEPTANCE_RECEIPT_VALID`; its 80 state/geometry checks, 24 Guide route activations, 390 exact copy actions, 12 no-JavaScript groups, configured-video and accessibility checks all record zero failures. All 20 Platform states across five viewports and four tab states have matching client/scroll widths with zero clipping or overlap.
- Verified request ownership records 33 requests and 13 unique `file:` URLs inside the fresh copied site root, with zero remote requests, root escapes, or repository back-references. External download and GitHub links remain visibly marked `需要联网` and are not runtime dependencies.

## Findings

None.
