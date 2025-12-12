v0.2.4

docs: fix markdown linter errors and broken links

- Fix broken link to Code of Conduct in `CONTRIBUTING.md`
- Fix broken anchor link to Installation section in `README.md`
- Standardize list styles (use hyphens) and indentation across all docs
- Add missing blank lines around headers, lists, and code blocks
- Remove trailing whitespace
- Apply formatting fixes to `docs/AGENTS.md`, `docs/DEPLOYMENT.md`, `docs/PRD.md`, `docs/USAGE.md`, and `docs/TODO.md`

docs: refactor documentation to third-person only and sync with codebase

- Refactor all documentation (`PRD.md`, `AGENTS.md`, `DEPLOYMENT.md`, `USAGE.md`, `README.md`) to use third-person, academic language, removing first-person references.
- Update `PRD.md` and `AGENTS.md` to reflect the upgrade to Tauri v2 for the Companion App.
- Remove references to React Query in `PRD.md` and `AGENTS.md`, confirming Zustand as the sole state management solution.
- Clarify search capabilities in `PRD.md`, explicitly distinguishing between backend-driven global search and client-side fuzzy transcript search.
- Standardize terminology and formatting across all documentation files to ensure consistency and accuracy with the current codebase state.
