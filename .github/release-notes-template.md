<!--
Maintainer: this is the body of the GitHub release, which Settings > Updates in
the app shows too. When the release is published, release.yml fills in the
version, the image digests and the commit list where the double-brace
placeholders stand. Keep all three, the link to the full notes and the two
collapsed blocks: Docs validation fails without them.

Rewrite the hand-written parts in the version-bump pull request, from
docs/releases/<version>.md, which scripts/cut_release_notes.py makes from
docs/releases/next.md. Docs validation caps the hand-written part at 350 words
and What's new at five bullets of at most 40 words each
(scripts/validate_docs.py). Everything else belongs in the full notes.
-->

## Nojoin {{VERSION}}

### Before you upgrade

<!-- One line for each breaking change and each step an operator must take, from Breaking changes and Upgrade in the full notes. Write "Nothing to do before upgrading." when there are none. -->

- **Each user accepts the terms of use.** Every account except the setup account accepts them once before it can record or import, and until then the API answers its new captures and imports with 403, so an API client acting for it needs that person to accept in the web app (#1007).

### What's new

<!-- At most five bullets, one sentence each, with no issue numbers. -->

- A name the AI suggests for a live speaker is withdrawn when the line it quoted moves to another speaker, and turning the suggestions off hides names already offered.
- **Test connection** with no model selected says the provider is reachable and asks you to choose a model, where it read as a passed test.
- On a Claude or ChatGPT subscription, the live naming ask stops after 90 s and **Generate structure** after 180 s, where both ran for up to 300 s.
- **Max Speakers** keeps its hint behind an "i" beside the box in the live toolbar, the import dialog and the reprocess dialog.
- An import the server refuses shows the server's message in the dialog, where the page fell to an error screen.

### Upgrade

Back up your instance, then update your checkout, pull the new images, stop the stack and start it again. Database migrations run on the first start.

```bash
git pull
docker compose pull
docker compose down
docker compose up -d
```

Every change, the migration, rollback steps and known issues are in the [full notes for {{VERSION}}](https://github.com/Valtora/Nojoin/blob/v{{VERSION}}/docs/releases/{{VERSION}}.md).

<details>
<summary>Container images</summary>

All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

</details>

<details>
<summary>Commits</summary>

{{CHANGELOG}}

</details>
