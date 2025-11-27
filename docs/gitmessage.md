fix(ui): improve settings search algorithm to prioritize exact matches

- **UI**: Update `getMatchScore` in `searchUtils.ts` to prioritize exact, starts-with, and substring matches over fuzzy matches.
- **UI**: Update `SettingsPage` to auto-switch tabs immediately on exact matches.
- **Docs**: Mark search improvement task as complete in `TODO.md`.
