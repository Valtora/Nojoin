## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Upgrade

Pull the new images and recreate the stack:

```bash
docker compose pull
docker compose up -d
```

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. -->

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->
Rollback is code-only unless a migration note above says otherwise: redeploy the previous image tags.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->
None known at release time.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->
No changes to browser-capture support in this release unless noted above.

### Changes

{{CHANGELOG}}
