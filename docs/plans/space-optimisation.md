# Space optimisation

Working plan for the second half of branch `feat/flat-ui-restyle`. This document is a PR-scoped
artefact and is removed in the final commit before merge. The durable rules it produces live in
[DESIGN.md](../DESIGN.md).

## 1. Purpose

The flat restyle fixed how Nojoin looks and left how it uses space untouched. On a 2560px display
roughly a quarter of the window is empty gutter, the dashboard's right column ends 40% down the
page leaving a dead L-shape, and the calendar card draws three concentric bordered surfaces inside
each other. This work spends that space, and fixes the structural cause rather than padding the
symptoms.

## 2. Decisions already made

Resolved with the maintainer before this plan was written. They are fixed inputs.

| # | Decision |
| --- | --- |
| 1 | Width policy is **per surface type**. Dense surfaces grow; prose surfaces keep a reading-width cap. |
| 2 | Dense surfaces cap at **120rem** and spend further width on **more columns, not wider ones**. |
| 3 | The dense set is **dashboard and people**. Tasks stays at 80rem, recording detail stays full-bleed, and `/recordings` is out (see decision 15). |
| 4 | All four viewport classes are in scope: ultrawide, 1920, laptop 1440-1536, tablet and phone. |
| 5 | The nav rail is unchanged: still ~340px, still resizable, still collapsible. |
| 6 | The dashboard gains **recent recordings**, **processing in flight**, and a standalone **agenda**. No counts strip. |
| 7 | The calendar's Month/Agenda toggle is **removed**. The month grid and the agenda become separate modules; the day view under the grid folds into the agenda module. |
| 8 | Columns are **equal height**; a module that overflows scrolls inside itself. The page still scrolls. |
| 9 | A module with nothing to show **does not render**, with calendar, Meet Now and tasks as an always-present floor. |
| 10 | **Card-on-card nesting is fixed everywhere it occurs**, not just at the worst offenders. See §4.4 for the corrected measurement: the figure quoted when this was decided was wrong. |
| 11 | Card headers collapse to **one compact row**; the explanatory descriptions are dropped. |
| 12 | On a phone the order is **action first**: Meet Now, processing, agenda, tasks, recents, month grid last. |
| 13 | Recent recordings **accepts the full-list fetch** and slices client-side. No backend change. |
| 14 | Same branch as the restyle, merged once. This plan is deleted at merge; the durable rules go to DESIGN.md. |
| 15 | **`/recordings` is left alone.** The rail-to-detail flow is untouched and the landing panel stays. Extra width would have nothing to fill: the list lives in the fixed-width rail, and the main area is prose. A grid or table there is a feature rather than a space optimisation, and is explicitly not in this branch. |
| 16 | **`RecordingCard.tsx` is deleted.** 375 lines, complete, tested and re-tokenised, imported by nothing in the entire git history. It was built for the recordings grid that decision 15 declines to build. `BatchActionBar.tsx` stays: it looked orphaned in a truncated search and is in fact live, driving batch selection in the recordings rail. |

## 3. Non-goals

- No backend changes. `getRecordings` keeps its current shape and the dashboard slices client-side,
  as the recordings rail already does.
- No widget system: module choice and order are fixed in code, not per-user state.
- No change to the nav rail, to recording detail's panel layout, or to the Tasks page width.
- No new dashboard data sources. Every module is built from data the app already fetches.
- No marketing site changes. Phase 5 of the restyle is complete.
- No new colour tokens. This is a layout change on the palette that already shipped.

## 4. Target specification

### 4.1 Width policy

A new `--workspace-max-width-dense` token at `120rem`, with a proportionally smaller compact value.
The existing caps are unchanged, so no prose surface moves:

| Token | Comfortable | Compact | Used by |
| --- | --- | --- | --- |
| `--workspace-max-width-dense` (new) | 120rem | 112rem | dashboard, people |
| `--workspace-max-width-wide` | 80rem | 74rem | tasks |
| `--workspace-max-width` | 72rem | 68rem | the default |
| `--workspace-max-width-feature` | 64rem | 60rem | auth, setup, consent, landing |

Reached through a `.workspace-shell-dense` class beside the existing three.

### 4.2 Column model

Three breakpoints, chosen so a column is never narrower than about 340px:

| Width | Columns |
| --- | --- |
| below 1024px | 1 |
| 1024 to 1599px | 2 |
| 1600px and above | 3 |

Columns are `items-stretch` rather than `items-start`, which is what removes the dead L-shape. A
module that would overflow its column takes `flex-1 min-h-0 overflow-auto` so it scrolls inside
itself. The page keeps its own scrollbar for the case where the tallest column exceeds the viewport,
which is the normal case on a laptop.

### 4.3 Dashboard modules

| Module | Floor | Source | Notes |
| --- | --- | --- | --- |
| Calendar (month grid) | always | existing calendar summary | loses the Month/Agenda toggle and the day view |
| Agenda | always | same fetch as the grid | absorbs the day view; shows what is next |
| Meet Now | always | none | compact header, button unchanged |
| Task list | always | existing tasks fetch | |
| Recent recordings | when non-empty | `getRecordings`, sliced client-side | status and click-through to detail |
| Processing in flight | when non-empty | the same response, filtered by status | refreshes on the existing `recording-updated` event, no new poller |

Desktop placement at three columns: the month grid leads (largest, left), the agenda and Meet Now
take the middle, tasks and the two recordings modules take the right. At two columns the third
column's modules fold into the second. At one column the order is decision 12's.

### 4.4 Surface nesting

The rule already in DESIGN.md, now enforced: two levels, and no card inside a card. The fix pattern:

| Depth | Before | After |
| --- | --- | --- |
| Outer | `bg-surface-card` + border + `shadow-card` | unchanged |
| Second | `bg-surface-card` + border + `shadow-card` | `bg-surface-inset`, no border, no shadow |
| Third | `bg-surface-card` + border + `shadow-card` | no surface at all: spacing and a divider |

**Corrected measurement.** The "49 sites across 15 files" quoted when this was scoped counted every
class string containing both `bg-surface-card` and `shadow-card`, which includes buttons, pills and
inputs. Those use the card fill as a control background and are not surfaces in the stack.

Measured against the canon's actual test, a *container* with a solid card fill and a hairline
border: **77 card containers across 38 files, of which 20 hold more than one.** A file with two
sibling cards is correct; only nesting is a defect, so each of the 20 is judged individually rather
than swept. Worst first: `DashboardUpcomingMeetingsCard` (7), `MeetingEdgePanel` (6),
`TasksWorkspace` (5), `ChatPanel` (4), `dashboardTasks/TaskRow` (4).

`DashboardUpcomingMeetingsCard` is **not** fixed in this phase. Phase D rewrites it to split the
month grid from the agenda, so un-nesting markup here would only be deleting it twice.

### 4.5 Card headers

One row: icon, title, and any count or action, at section-heading size rather than `text-2xl`. The
icon chip loses its `rounded-2xl p-2` treatment. The explanatory sentence under the title is
removed. Roughly 90px recovered per card, against three cards today and six after this work.

## 5. Phase plan

Each phase is one reviewable commit or a small set, gated by `npm run lint`, `npm run test`,
`npm run check:contrast` and `npm run build`.

### Phase A: width and column foundation

- `--workspace-max-width-dense` and `.workspace-shell-dense` in `globals.css`, both densities.
- Point the dashboard and the people page at the dense shell. The people page uses a hard-coded
  `max-w-7xl` rather than the workspace shell, so it moves onto the token in the same change.
- Dashboard grid moves to three breakpoints with `items-stretch`.
- Delete the orphaned component of decision 16, and correct the five places in the docs that
  tell contributors to keep a non-existent grid view in sync. The shared-action invariant test is
  rewritten rather than removed: the hook has two live consumers, `Sidebar` and
  `RecordingStatusDisplay`, so the invariant is real and merely names the wrong second surface.
- No module changes yet, so the diff is small and the effect is visible immediately.

### Phase B: nesting

- The 20 multi-card files of §4.4, each judged individually: sibling cards are left alone, nested
  ones are flattened to the pattern above.
- Excludes `DashboardUpcomingMeetingsCard`, which Phase D rewrites.
- Verified per surface rather than as one sweep, because this is the phase most likely to change
  something that was deliberate.

### Phase C: card headers

- The compact header row, applied to the dashboard cards first and then to any other surface using
  the icon-chip pattern.

### Phase D: dashboard modules

- Split the calendar card: month grid and agenda become separate modules, the toggle and the day
  view are removed.
- Add recent recordings and processing in flight, both from one fetch.
- Empty-module hiding, with the three-module floor.
- Mobile ordering per decision 12.

### Phase E: close-out

- DESIGN.md gains the durable layout section: width policy per surface, the column model, the
  surface-nesting rule made explicit, and the module rules.
- Verification matrix (§6).
- Delete this plan document.

## 6. Verification

Automated, per phase: `npm run lint`, `npm run test`, `npm run check:contrast`, `npm run build`.
The contrast gate is unaffected by layout but runs anyway, because a nesting change can swap a
token.

Manual matrix. No screenshots are captured; the maintainer reviews directly.

- **Widths:** 375px, 768px, 1440px, 1920px, 2560px. The three column breakpoints are 1024 and 1600,
  so 1440 and 1920 sit either side of the second one deliberately.
- **Surfaces:** dashboard (all six modules, and with modules empty), recordings landing, people
  (table and card stack), tasks, settings, recording detail, live capture.
- **Axes:** light and dark, comfortable and compact.
- **States:** a fresh account showing only the floor modules; an active account showing all six; an
  account with something processing.

## 7. Definition of done

1. No surface renders a card inside a card: the second level is an inset and the third is bare.
2. The dashboard has no dead column at any width; columns end level.
3. Dense surfaces fill to 120rem and add a third column at 1600px.
4. Modules with nothing to show do not render, and the floor never drops below three.
5. The Month/Agenda toggle is gone and both views are visible at once above 1024px.
6. `npm run lint`, `npm run test`, `npm run check:contrast`, `npm run build` green.
7. DESIGN.md describes the shipped layout rules; this plan file is deleted.
8. The matrix in §6 has been executed with no open regressions.

## 8. Risks

- **The nesting sweep is the largest blast radius**: 49 sites across surfaces the maintainer has
  not asked to change. Mitigation is per-surface commits and per-surface review, and the fact that
  the rule being enforced is already documented.
- **Equal-height columns create nested scroll regions.** On touch, a scroll region inside a
  scrolling page can trap a gesture. Mitigated by the fact that at one column no module is
  height-constrained, so the nesting only exists at 1024px and above.
- **Six modules is more dashboard than three.** If the result feels busy rather than useful, the
  floor plus empty-hiding means a quiet account still sees three, but a busy one cannot opt out
  without a widget system, which decision 6 explicitly rejected.
- **The full-list fetch grows with the library.** Accepted in decision 13, and identical to what
  the recordings rail already does, but a user with thousands of recordings pays it on the
  dashboard as well as on `/recordings`.
