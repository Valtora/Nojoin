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

          The third column only exists when something fills it. The task list
          lives there when it does, and folds back beside the agenda when it
          does not, so the absence of recordings is a two-column layout rather
          than a column holding one lonely module.

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
          band under them. `min-height` rather than `flex-grow`, because content
          taller than the window has to push past it rather than be clipped.

          The 64rem ceiling is the one arbitrary number here. Without it a very
          tall display stretches whichever module is flexible, and a quiet day
          would give you a 1300px empty agenda. Above the ceiling the leftover
          sits under the grid, where it reads as page rather than as a card that
          failed to fill. */}
      <div className="@container flex grow flex-col">
        <section
          className={`flex flex-col gap-[var(--workspace-gap)] @min-[54rem]:grid @min-[54rem]:min-h-[min(100%,64rem)] @min-[54rem]:grid-cols-[minmax(0,1.1fr)_minmax(20rem,1fr)] @min-[54rem]:items-stretch ${
            hasThirdColumn
              ? "@min-[74rem]:grid-cols-[minmax(0,1.25fr)_minmax(18rem,1fr)_minmax(16rem,0.85fr)]"
              : ""
          }`}
        >
          {/* `row-span-2`, not `row-end-[-1]`. A negative grid line counts back
              from the end of the *explicit* grid, and this grid declares no
              rows at all, so `-1` resolves to line 1 and the span silently
              collapses to a single row. That left the month grid ending level
              with the agenda and a dead corner underneath it, which is the
              exact defect this layout exists to remove. At three columns the
              second row is gone, so the span goes back to one. */}
          <div
            id="dashboard-upcoming-meetings"
            className={`order-6 flex min-w-0 flex-col @min-[54rem]:col-start-1 @min-[54rem]:row-start-1 @min-[54rem]:row-span-2 ${
              hasThirdColumn ? "@min-[74rem]:row-span-1" : ""
            }`}
          >
            <MonthGridCard calendar={calendar} />
          </div>

          {/* The `order` values are the phone sequence. This column is the one
              place they need overriding, because on a desktop the task list
              sits above the agenda while on a phone the agenda comes first. The
              two orders are independent by design; that is the whole point of
              the modules being direct grid children. */}
          <div className="contents @min-[54rem]:col-start-2 @min-[54rem]:row-start-1 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)]">
            <div
              id="dashboard-task-cards"
              className="order-4 flex min-h-0 flex-1 flex-col @min-[54rem]:order-1"
            >
              <DashboardTasksPanel />
            </div>

            <div
              id="dashboard-agenda"
              className="order-3 flex min-h-0 flex-1 flex-col @min-[54rem]:order-2"
            >
              <AgendaCard calendar={calendar} />
            </div>
          </div>

          <div
            className={`contents @min-[54rem]:col-start-2 @min-[54rem]:row-start-2 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)] ${
              hasThirdColumn ? "@min-[74rem]:col-start-3 @min-[74rem]:row-start-1" : ""
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
