"use client";

import Workspace from "./Workspace";
import DashboardTasksPanel from "./DashboardTasksPanel";
import DashboardUpcomingMeetingsCard from "./DashboardUpcomingMeetingsCard";
import MeetingControls from "./MeetingControls";

export default function DashboardHome() {
  return (
    <Workspace
      contentClassName="workspace-shell workspace-shell-dense"
      paddingClassName="workspace-pad-y"
    >
      {/* Two columns from xl, one below it. The breakpoint stays at xl rather
          than dropping to lg because these are viewport widths and the
          workspace sits beside a ~340px rail: at a 1024px viewport the content
          area is about 620px, which is under the 20rem the second column asks
          for, and the grid would overflow rather than fit.

          The third column arrives with the modules that fill it; declaring it
          now would only add an empty column, which is worse than the gutter it
          replaces.

          `items-stretch` is what removes the dead corner this layout used to
          have: with `items-start` each column ended wherever its content did,
          so the shorter one left an empty L-shape down the page. Stretching
          makes the columns end level, and a module that overflows scrolls
          inside itself rather than pushing the grid taller. */}
      <section className="flex flex-col gap-[var(--workspace-gap)] xl:grid xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)] xl:items-stretch">
        <div
          id="dashboard-upcoming-meetings"
          className="flex min-w-0 flex-col xl:col-start-1 xl:row-start-1"
        >
          <DashboardUpcomingMeetingsCard />
        </div>

        <div className="flex min-w-0 flex-col gap-[var(--workspace-gap)] xl:col-start-2 xl:row-start-1">
          <div id="dashboard-meeting-controls">
            <MeetingControls
              variant="dashboard"
              onMeetingEnd={() => {
                window.dispatchEvent(new Event("recording-updated"));
              }}
            />
          </div>

          <div id="dashboard-task-cards" className="flex min-h-0 flex-1 flex-col">
            <DashboardTasksPanel />
          </div>
        </div>
      </section>
    </Workspace>
  );
}
