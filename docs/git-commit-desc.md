# Git Commit Description - Use Conventional Commit Guidelines

chore: remove redundant frontend .gitignore

Deleted `frontend/src/app/(dashboard)/.gitignore` as it was a duplicate of the root configuration and incorrectly placed.

fix(security): implement RBAC and fix SSRF in system/setup endpoints

- Added `get_current_admin_user` dependency to enforce Owner/Admin roles.
- Secured `system.py` endpoints (model download, deletion) to require admin privileges.
- Implemented conditional authentication for `setup.py` (LLM validation) to prevent SSRF while allowing initial setup.
- Updated `audit_script.py` to verify security fixes.

feat(ui): improve tag system UX and consistency

- Fix visibility of newly created tags in AddTagModal by explicitly reloading tags.
- Enable toggle functionality for selecting/deselecting tags in AddTagModal.
- Update tag styling in RecordingTagEditor to match sidebar design (neutral pill with colored dot).
