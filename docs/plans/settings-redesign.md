# Implementation Plan: Settings Information Architecture and UI Redesign

Status: **approved, in progress.** Branch: `refactor/settings-ia-redesign`.

> **This document is temporary.** It is a working artefact for the duration of this
> work. Delete it in the final commit of the redesign. Anything worth keeping is
> promoted into [USAGE.md](../USAGE.md), [ADMIN.md](../ADMIN.md), or an ADR before
> deletion.

## 1. Problem

Settings has six categories holding roughly thirty sections. Two of those categories
carry most of the weight: AI holds eleven sections and Administration holds seven
heavyweight tools. The page is dense, and the density is unevenly distributed.

The visual inconsistency has three concrete causes, not one:

1. **Double width constraint.** `SettingsPage` wraps content in `mx-auto max-w-4xl`,
   and each `SettingsSection` re-constrains itself with a `width` prop resolving to
   `max-w-2xl`, `max-w-3xl`, `max-w-4xl`, or `max-w-none`. Across the tabs that is 8
   compact, 7 regular, 8 wide, and 6 full, all left-aligned in the same column, so
   card edges do not line up.
2. **Non-monotonic elevation.** The page is `dark:bg-gray-900`, a section is
   `dark:bg-gray-950/90` (darker than the page), a nested `field` panel is
   `gray-950/80` (the same as its parent), and a nested `subtle` panel is
   `gray-900/70`, which is lighter than the card containing it. Depth carries no
   consistent meaning.
3. **Settings is opted out of the app's design tokens.** `globals.css` defines
   `--surface-radius`, `--surface-padding`, and `--workspace-max-*`, plus a full
   compact-density variant under `html[data-ui-density="compact"]`. No settings
   component references any of them; they use raw Tailwind values, so seven distinct
   border radii are in play and Settings is the only area of Nojoin that does not
   respond to compact density.

Two information-architecture defects compound this:

- **The AI category means different things to different people.** Five of its
  sections render only for admins, so a non-admin sees a six-section page and an
  owner sees an eleven-section page under the same label.
- **"Capture" already means two things.** Personal holds "Capture defaults" (voice
  activity detection, speaker diarization) while the Capture category holds browser
  microphone and gain settings.

## 2. Information architecture

Organised **by domain**, not by role. Admin-only content is badged `Install-wide` and
lives in its domain rather than in a separate administration area. Existing
per-section admin gating is unchanged: non-admins do not see admin-only sections, and
categories composed entirely of them do not appear in the navigation.

**Fourteen categories in four groups.** Admins see fourteen; non-admins see nine.

| Group | Category | Route | Visible to | Contents |
| --- | --- | --- | --- | --- |
| General | Profile and security | `/settings/profile` | all | username, password |
| | Users and access | `/settings/users` | admin | users, invitations |
| | Appearance | `/settings/appearance` | all | theme, timezone, spellcheck |
| | Integrations | `/settings/integrations` | mixed | calendar connections, connected apps (MCP), calendar OAuth credentials (admin) |
| Meetings | Recording | `/settings/recording` | all | device, microphone gain, shared-audio gain, automatic levels, live input test, browser processing, VAD, diarization |
| | Transcription | `/settings/transcription` | mixed | language, glossary, engine (admin) |
| | Notes and live assistance | `/settings/notes` | all | notes structure, automatic enhancement, Meeting Edge |
| | Your AI | `/settings/your-ai` | all | AI routing, Claude and ChatGPT subscription connect |
| | AI providers | `/settings/ai-providers` | admin | primary provider, fallback, Hugging Face token, model assets, CLI usage and quota |
| Data | Backup and restore | `/settings/backup` | admin | export, restore |
| | Privacy | `/settings/privacy` | admin | anonymous usage data |
| | System and logs | `/settings/system` | admin | live logs, infrastructure |
| About | Updates | `/settings/updates` | all | release overview, snapshot, history |
| | Help | `/settings/help` | all | tours, report a bug |

### Resolved naming collisions

- **Capture.** VAD and speaker diarization move out of Personal into **Recording**,
  alongside the microphone and gain controls. The word "capture" no longer names a
  category.
- **People.** The settings category for user accounts is **Users and access**. It is
  never called People, which already means the contacts and voiceprint library in the
  main navigation.

### Why AI splits into two categories

`AiRoutingSection` renders for every user and contains `CliOAuthPanel`, the per-user
Claude and ChatGPT subscription connect flow. An admin-only AI category would remove a
feature non-admins need. Keeping it as a single category would also produce an
eight-section page for admins, which reproduces the original problem in a new place.

**Your AI** (all users) holds the routing choice and subscription connect. **AI
providers** (admin) holds provider, fallback, token, model assets, and quota.

## 3. Shell architecture

- **Real routes per category** replace `?tab=`. Legacy values redirect permanently:
  `audio`, `companion`, `capture` to `/settings/recording`; `general`, `account`,
  `personal` to `/settings/profile`; `admin`, `administration` to `/settings/users`;
  `ai` to `/settings/your-ai`. The existing `frontend/src/app/(dashboard)/settings/capture/page.tsx`
  redirect stub is repointed.
- **State hoists to `settings/layout.tsx`.** One provider owns the fetch, the settings
  object, the one-second debounce, and the footer status. This is required, not
  cosmetic: `useDebouncedAutosave` clears its pending timer on unmount without
  flushing, so per-route ownership would silently discard a write when the user
  changes category within a second of editing.
- **Search becomes a cross-category results list** backed by the settings registry
  (section 6). Selecting a result routes to the category and scrolls to the setting.
  Keystroke-driven auto-switching is dropped; under real routes it would push a
  history entry per character.
- **Two heading levels.** A compact global identity strip, then a per-category H1 and
  description. Section eyebrows are removed as redundant; all six Administration
  sections currently carry `eyebrow="Administration"`.
- **Mobile navigates by drill-in.** Below `lg`, `/settings` renders the grouped
  category list and tapping a category opens it with a back link. The six-chip
  horizontal strip does not scale to fourteen items in four groups.
- **Forced password change is preserved.** When `force_password_change` is set, the
  layout provider redirects every category route to `/settings/profile` and suppresses
  the navigation, matching today's single-tab lockout.

## 4. Advanced gate

A per-page collapsible block at the bottom of a category.

**Advanced is orthogonal to admin.** Admin answers who may change a setting; advanced
answers how likely anyone is to need it. A setting can be either, both, or neither.
Conflating them would make the gate useless on a single-user install, where the owner
is the admin.

**Criteria.** A setting is gated if it meets any of:

1. it has a safe default and is rarely changed;
2. it can silently degrade output if set wrong;
3. it needs external credentials or knowledge;
4. it only matters at scale or on a multi-user install.

**Override — never gate a page's primary purpose.** Notes templates, invitations,
telemetry, the transcription engine, and the routing choice are exempt on their own
pages, even where a criterion matches.

**Floor rule.** If gating would leave a page with nothing visible, the page shows
everything and has no Advanced block. Privacy renders its single toggle plainly.

**Behaviour.** Collapsed on every visit. Auto-expands and highlights when a search
result lives inside it. The header shows a count when any contained value differs from
its default, for example `Advanced · 2 changed`.

**Result.** Seven of fourteen categories carry a gate: Integrations, Recording,
Transcription, Notes and live assistance, Your AI, AI providers, System and logs. The
other seven show everything.

## 5. Visual system

- **One width.** The `width` prop is deleted. Every section fills a single reading
  column so left and right edges align down the page. Data-heavy pages — Users, System
  and logs, CLI usage — declare full-bleed once at page level rather than per section.
- **Two container levels, never three.** One card per section; inside it, rows with
  label and description on the left and the control on the right, separated by hairline
  dividers. No card contains a card. Composite UI — the live input meter, waveform,
  users table, log console — uses a full-width **block slot** inside the same card,
  with no border of its own.
- **Tokens from `globals.css`.** Settings consumes `--surface-radius`,
  `--surface-padding`, and `--workspace-max-*` instead of raw Tailwind values. No
  renaming and no new dependency: the values already match the design system exactly.
  The immediate benefit is that Settings finally honours
  `html[data-ui-density="compact"]`. This also collapses the seven radii currently in
  use onto the token scale.

## 6. Settings registry

A single manifest with one entry per setting: id, label, category, keywords, admin
requirement, advanced flag with its criterion, and **default value**. It powers search
results, Advanced assignment, and the changed-count badge, and it replaces the 46
inline default fallbacks currently scattered across the settings components.

**Accepted risk, stated plainly:** defaults are declared in the frontend while the
backend holds its own inline, for example `ctx.merged_config.get("enable_vad", True)`
in `backend/worker/tasks/pipeline.py`. A backend default change without a matching
registry update makes the badge count wrong. This is cosmetic but real. Serving
defaults from the API is the follow-up if it becomes a problem.

## 7. Scope boundaries

**Rebuilt:** the shell, navigation, routing, primitives, registry, search, Advanced
gate, and the section-level layout of every category.

**Re-homed and shell-restyled only:** Users, System and logs, Backup and restore,
Invitations, CLI usage, calendar providers, and the five settings modals. They adopt
the outer card, token radii and colours, and the block slot, so they stop looking
foreign, but their tables, consoles, and wizards keep their current interaction
design. Roughly 2,600 lines of thinly tested interaction logic is deliberately left
alone.

**Not in scope:** backend changes, a defaults API, a command palette, and any new
settings.

## 8. Documentation updates

The redesign renames user-visible paths, so 26 references across the docs are stale
and must be updated in the same change:

| File | References |
| --- | --- |
| [ADMIN.md](../ADMIN.md) | calendar providers, AI provider defaults, notes structure and glossary, language preferences, anonymous usage data, backup and restore, system operations, updates, capture troubleshooting |
| [USAGE.md](../USAGE.md) | capture setup, Meeting Edge, notes structure, glossary, connected apps, language preferences, AI routing, CLI usage and quota, secondary provider, microphone troubleshooting |
| [CALENDAR.md](../CALENDAR.md) | provider credentials, personal connections, live sync |
| [TELEMETRY.md](../TELEMETRY.md) | opt-out path, in-app instructions |
| [DEPLOYMENT.md](../DEPLOYMENT.md) | model download prompt, telemetry env override, system logs, updates |
| [MCP.md](../MCP.md) | connected apps path |

## 9. Verification

Automated, added to the frontend vitest suite as `settingsRegistry.test.ts`:

- every key in the `Settings` type is registered exactly once;
- no key is registered twice;
- every category retains at least one visible section after gating (the floor rule);
- every advanced entry cites a criterion;
- every registry category has a route, and every route has a registry category;
- every legacy `?tab=` value redirects to a real route.

This guards the one failure mode with no visible symptom: a section imported by no
page still builds, still lints, and still passes every existing test, while rendering
nowhere.

`settingsMetadata.test.ts` and `settingsState.test.ts` are rewritten against the new
model. `useDebouncedAutosave.test.tsx`, `aiSettingsModels.test.ts`,
`AiTranscriptionSection.test.tsx`, and `NotesTemplatesSection.test.tsx` survive
unchanged.

Local checks before commit:

```bash
cd frontend
npm run lint
npm run test
npm run build

cd ..
python3 scripts/validate_docs.py
```

Manual smoke: every category at 375, 768, and 1440 pixels, in light and dark, as
admin and as non-admin; autosave persisting across a category change within the
debounce window; search reaching a setting inside a collapsed Advanced block; forced
password change locking navigation to Profile.

## 10. Commit sequence

Delivered as one pull request, structured as sequential commits so review and bisect
remain practical:

1. shell — routes, layout provider, registry, primitives, navigation, mobile index,
   with existing components mounted unchanged;
2. General group — Profile and security, Users and access, Appearance, Integrations;
3. Meetings group — Recording, Transcription, Notes and live assistance, Your AI, AI
   providers;
4. Data and About groups — Backup and restore, Privacy, System and logs, Updates,
   Help;
5. cleanup — delete superseded primitives, update documentation, delete this plan.
