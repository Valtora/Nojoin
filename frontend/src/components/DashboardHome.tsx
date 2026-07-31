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
      contentClassName="workspace-shell workspace-shell-dense"
      paddingClassName="workspace-pad-y"
    >
      {/* Two columns from xl, three from 1600px, one below. The breakpoints are
          viewport widths and the workspace sits beside a ~340px rail, so the
          content area is always narrower than the number in the class: at a
          1024px viewport it is about 620px, which is why the second column
          waits for xl rather than lg, and the third waits for 1600 rather than
          2xl.

          The third column only exists when something fills it. The task list
          lives there when it does, and folds back beside the agenda when it
          does not, so the absence of recordings is a two-column layout rather
          than a column holding one lonely module.

          `items-stretch` is what removes the dead corner this layout used to
          have: with `items-start` each column ended wherever its content did,
          so the shorter one left an empty L-shape down the page. Stretching
          makes the columns end level, and a module that overflows scrolls
          inside itself rather than pushing the grid taller.

          Below xl the two wrappers are `display: contents`, which takes them
          out of the box tree and makes all six modules direct children of one
          flex column. That is what lets `order-*` put them in the phone order
          decided for this dashboard, which is action first: Meet Now, whatever
          is processing, the agenda, tasks, recents, and the month grid last. A
          wrapper cannot reorder across its own boundary, so without this the
          modules would come out grouped by desktop column. */}
      <section
        className={`flex flex-col gap-[var(--workspace-gap)] xl:grid xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)] xl:items-stretch ${
          hasThirdColumn
            ? "min-[1600px]:grid-cols-[minmax(0,1.1fr)_minmax(20rem,0.8fr)_minmax(20rem,0.8fr)]"
            : ""
        }`}
      >
        <div
          id="dashboard-upcoming-meetings"
          className="order-6 flex min-w-0 flex-col xl:col-start-1 xl:row-start-1 xl:row-end-[-1]"
        >
          <MonthGridCard calendar={calendar} />
        </div>

        <div className="contents xl:flex xl:min-w-0 xl:flex-col xl:gap-[var(--workspace-gap)] xl:col-start-2 xl:row-start-1">
          <div id="dashboard-meeting-controls" className="order-1">
            <MeetingControls
              variant="dashboard"
              onMeetingEnd={() => {
                window.dispatchEvent(new Event("recording-updated"));
              }}
            />
          </div>

          <div id="dashboard-agenda" className="order-3 flex min-h-0 flex-1 flex-col">
            <AgendaCard calendar={calendar} />
          </div>
        </div>

        <div
          className={`contents xl:flex xl:min-w-0 xl:flex-col xl:gap-[var(--workspace-gap)] xl:col-start-2 xl:row-start-2 ${
            hasThirdColumn ? "min-[1600px]:col-start-3 min-[1600px]:row-start-1" : ""
          }`}
        >
          {showProcessing && (
            <div id="dashboard-processing" className="order-2">
              <ProcessingCard recordings={processing} />
            </div>
          )}

          <div id="dashboard-task-cards" className="order-4 flex min-h-0 flex-1 flex-col">
            <DashboardTasksPanel />
          </div>

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
    </Workspace>
  );
}
