# Git Commit Description

ci: unify release workflow and implement lock-step versioning

- **Unified Workflow:** Replaced separate `docker-publish.yml` and `companion-tauri.yml` with a single `release.yml` workflow. This new workflow handles both Docker image builds and Companion App compilation for every release tag.
- **Lock-step Versioning:** Adopted "Strategy A" (Lock-step Versioning) where every Server release (`vX.Y.Z`) produces a corresponding Companion App release with the exact same version number.
- **Auto-Sync Script:** Created `scripts/sync-version.js` and integrated it into the CI pipeline. This script automatically synchronizes the version from the Git Tag (or `docs/VERSION`) to `companion/package.json`, `companion/src-tauri/tauri.conf.json`, and `companion/src-tauri/Cargo.toml` during the build process, eliminating manual version management errors.
- **Documentation:** Updated `AGENTS.md`, `DEPLOYMENT.md` and `PRD.md` to reflect the new release strategy and automated versioning pipeline.
- **Cleanup:** Removed obsolete workflow files.
