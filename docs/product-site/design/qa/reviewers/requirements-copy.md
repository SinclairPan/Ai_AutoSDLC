# Independent requirement, copy, and product-fact reviewer attestation

Reviewer role: `requirements-copy`
Reviewer ID: `reviewer-requirements-copy-75d6b21`
Reviewer task: `/root/startup_cost_review`
Reviewed product baseline: `75d6b216bd2c4806ecfbe5094029a70e271e4460`
Reviewed at UTC: `2026-08-22T04:44:52Z`
Verdict: `PASS`
Finding count: `0`
Canonical content SHA256: `64bbf9f24d8a8e2e3c32c99784148febde064ac50afd5a3d554cc04551f5c2b8`
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

Independently reviewed the exact v3.0.1 product baseline, required source copy, visual requirements, shipped product pages, Chinese first-user guide, Loop and dynamic-expert mechanism claims, platform-capability claims, release links and asset identities, three-layer synthetic benefit data, and the persisted package/browser evidence. The review distinguishes evidence-anchored synthetic marketing evaluation from production measurement, statistical significance, SLA evidence, and causal proof.

## Independent verification

- Read the reviewed requirement, copy, guide, release, data, receipt, and runner inputs from exact commit `75d6b216bd2c4806ecfbe5094029a70e271e4460` and recomputed all 18 SHA-256 values from Git blob bytes.
- Confirmed the annotated `v3.0.1` tag object resolves to commit `9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4` and tree `fd5c2dac0a216f0eb17855d03cc7900d872d3c61`; the embedded Chinese guide is byte-identical to the tagged source and the release asset names and SHA-256 values agree across source copy and Downloads & Docs.
- Confirmed the source guide, generated selector, generated route sections, Downloads & Docs copy, and visual specification consistently describe 12 self-contained routes: empty or existing project, online or offline acquisition, across Windows AMD64, macOS Apple Silicon, and Linux AMD64.
- Checked Loop, dynamic-expert, and platform wording against the v3.0.1 source contract: five Loop types, conditional frontend and local-PR paths, at most two fresh read-only experts, findings returned to the original Writer, at most one rereview, fail-closed review failure, local-first boundaries, and explicit human confirmation remain accurately bounded.
- Recomputed the three shipped JSON hashes and matched them to the current report and package manifest. Confirmed the site and report explicitly state that the existing `gpt-5.6-sol/high` synthetic values were retained while v3.0.1 capability evidence and metadata were rebound, with zero new Provider sessions and no numerical rerun.
- Verified the benefit surfaces disclose advantage-aligned selection, non-production status, and limits against statistical, SLA, general-production, or causal interpretation. Ran the complete offline product-site and v3 release unit scope: 117 tests passed.

## Findings

None.
