"use client";

import { useEffect, useState } from "react";

import { getBrowserTimeZone } from "@/lib/timezone";

import Workspace from "./Workspace";
import DashboardTasksPanel from "./DashboardTasksPanel";
import MeetingControls from "./MeetingControls";
import ProcessingCard from "./dashboardRecordings/ProcessingCard";
import RecentRecordingsCard from "./dashboardRecordings/RecentRecordingsCard";
import { useDashboardRecordings } from "./dashboardRecordings/useDashboardRecordings";
import AgendaCard from "./upcomingMeetings/AgendaCard";
import MonthGridCard from "./upcomingMeetings/MonthGridCard";
import { useCalendarDashboard } from "./upcomingMeetings/useCalendarDashboard";

export default function DashboardHome() {
  const { recent, processing } = useDashboardRecordings();
  // The month grid and the agenda are two modules over one subsystem, so the
  // dashboard owns the fetch and hands the same state to both. Calling the hook
  // inside each module would request the month summary twice.
  const calendar = useCalendarDashboard();
  const [timeZone, setTimeZone] = useState("UTC");

  // Resolved after mount rather than during render, because the server has no
  // browser time zone and a mismatch would hydrate differently.
  useEffect(() => {
    setTimeZone(getBrowserTimeZone());
  }, []);

  // A module with nothing to say does not render. The calendar, the agenda,
  // Meet Now and the task list are the floor, so a new account sees a dashboard
  // rather than a wall of empty boxes, and an active one fills the grid.
  const showRecents = recent.length > 0;
  const showProcessing = processing.length > 0;
  const hasThirdColumn = showRecents || showProcessing;

  return (
    <Workspace
      contentClassName="workspace-shell workspace-shell-dense grow"
      paddingClassName="workspace-pad-y"
      backgroundClassName="bg-surface-page flex flex-col"
    >
      {/* One column, then two from 54rem, then three from 74rem, measured
          against the workspace rather than the viewport.

          It has to be the workspace. The nav rail is ~340px, resizable and
          collapsible, so the space this grid actually has is the viewport minus
          a number the grid cannot see. Viewport breakpoints get that wrong in
          both directions: they withhold a column from someone who collapsed the
          rail, and they promise one to someone who widened it. A container
          query asks the only question that matters, which is how wide this
          content area is right now.

          The thresholds are where a column would otherwise fall below about
          340px: at 54rem two columns are ~420px each, and at 74rem three are
          ~340px each.

          Columns group by subject. The calendar owns the first: the month grid
          with the agenda under it, since they are two views of one subsystem.
          The task list has the second to itself, so it grows to the full height
          of the page. Capture owns the third, with whatever it produced under
          it: Meet Now, anything still processing, then recent recordings.

          The third column only exists when something fills it. Capture folds
          back into the second when there are no recordings at all, so a fresh
          account gets a two-column layout rather than a column holding one
          lonely button.

          `items-stretch` is what removes the dead corner this layout used to
          have: with `items-start` each column ended wherever its content did,
          so the shorter one left an empty L-shape down the page. Stretching
          makes the columns end level, and a module that overflows scrolls
          inside itself rather than pushing the grid taller.

          At one column the two wrappers are `display: contents`, which takes
          them out of the box tree and makes all six modules direct children of
          one flex column. That is what lets `order-*` put them in the phone
          order decided for this dashboard, which is action first: Meet Now,
          whatever is processing, the agenda, tasks, recents, and the month grid
          last. A wrapper cannot reorder across its own boundary, so without
          this the modules would come out grouped by desktop column. */}
      {/* The grid takes the height the window has left, so the columns reach the
          bottom of the viewport instead of stopping short and leaving a dead
          band under them.

          It is `grow` the whole way down rather than a percentage height. A
          percentage needs the parent's height to be *definite*, and this chain
          hands height down through flex-grow from a container that is
          `height: auto` with `min-height: 100%`; a percentage against that
          computes to zero and the declaration silently does nothing. Flex
          growth has no such requirement, because it distributes free space
          during layout rather than resolving against a declared size. `grow`
          rather than `flex-1` specifically: growth only, never shrink, so
          content taller than the window pushes past instead of being squeezed.

          Filling is uncapped, which is a deliberate trade rather than an
          oversight. Every way of capping it breaks something else: a max-height
          on the grid leaves the box shorter than its own content once a column
          is genuinely long, and a max-height on the modules makes one column's
          cards end above the others, which is the dead corner this layout
          exists to remove. If a very tall display ever makes an empty module
          look silly, cap that module rather than the grid. */}
      <div className="@container flex grow flex-col">
        <section
          className={`flex grow flex-col gap-[var(--workspace-gap)] @min-[54rem]:grid @min-[54rem]:grid-cols-[minmax(0,1.1fr)_minmax(20rem,1fr)] @min-[54rem]:items-stretch ${
            hasThirdColumn
              ? "@min-[74rem]:grid-cols-[minmax(0,1.25fr)_minmax(18rem,1fr)_minmax(16rem,0.85fr)]"
              : ""
          }`}
        >
          {/* The calendar column. `row-span-2`, not `row-end-[-1]`: a negative
              grid line counts back from the end of the *explicit* grid, and
              this grid declares no rows at all, so `-1` resolves to line 1 and
              the span silently collapses to a single row. At three columns the
              second row is gone, so the span goes back to one.

              The `order` values are the phone sequence, which runs agenda then
              month grid. On a desktop the grid leads and the agenda sits under
              it, so this column overrides them. The two sequences are
              independent by design; that is what the modules being direct grid
              children buys. */}
          <div
            className={`contents @min-[54rem]:col-start-1 @min-[54rem]:row-start-1 @min-[54rem]:row-span-2 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)] ${
              hasThirdColumn ? "@min-[74rem]:row-span-1" : ""
            }`}
          >
            <div
              id="dashboard-upcoming-meetings"
              className="order-6 flex min-w-0 flex-col @min-[54rem]:order-1"
            >
              <MonthGridCard calendar={calendar} />
            </div>

            <div
              id="dashboard-agenda"
              className="order-3 flex min-h-0 flex-1 flex-col @min-[54rem]:order-2"
            >
              <AgendaCard calendar={calendar} />
            </div>
          </div>

          {/* The task list has its column to itself, so it runs the full height
              of the page. At two columns it sits under capture instead. */}
          <div
            id="dashboard-task-cards"
            className={`order-4 flex min-h-0 flex-1 flex-col @min-[54rem]:col-start-2 @min-[54rem]:row-start-2 ${
              hasThirdColumn ? "@min-[74rem]:row-start-1" : ""
            }`}
          >
            <DashboardTasksPanel />
          </div>

          {/* Capture, and what it produced. Meet Now keeps its natural height;
              recent recordings takes everything below it. */}
          <div
            className={`contents @min-[54rem]:col-start-2 @min-[54rem]:row-start-1 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)] ${
              hasThirdColumn ? "@min-[74rem]:col-start-3" : ""
            }`}
          >
            <div id="dashboard-meeting-controls" className="order-1">
              <MeetingControls
                variant="dashboard"
                onMeetingEnd={() => {
                  window.dispatchEvent(new Event("recording-updated"));
                }}
              />
            </div>

            {showProcessing && (
              <div id="dashboard-processing" className="order-2">
                <ProcessingCard recordings={processing} />
              </div>
            )}

            {showRecents && (
              <div
                id="dashboard-recent-recordings"
                className="order-5 flex min-h-0 flex-1 flex-col"
              >
                <RecentRecordingsCard recordings={recent} timeZone={timeZone} />
              </div>
            )}
          </div>
        </section>
      </div>
    </Workspace>
  );
}
