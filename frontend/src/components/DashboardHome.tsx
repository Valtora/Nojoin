"use client";

import AmbientWorkspace from "./AmbientWorkspace";
import DashboardTasksPanel from "./DashboardTasksPanel";
import DashboardUpcomingMeetingsCard from "./DashboardUpcomingMeetingsCard";
import MeetingControls from "./MeetingControls";

export default function DashboardHome() {
  return (
    <AmbientWorkspace
      contentClassName="max-w-7xl gap-6"
      paddingClassName="py-3 md:py-5"
    >
      <section className="flex flex-col gap-6 xl:grid xl:grid-cols-[minmax(0,1.15fr)_minmax(22rem,0.85fr)] xl:items-start">
        <div id="dashboard-upcoming-meetings" className="xl:col-start-1 xl:row-start-1">
          <DashboardUpcomingMeetingsCard />
        </div>

        <div className="flex flex-col gap-6 xl:col-start-2 xl:row-start-1">
          <div id="dashboard-meeting-controls">
            <MeetingControls
              variant="dashboard"
              onMeetingEnd={() => {
                window.dispatchEvent(new Event("recording-updated"));
              }}
            />
          </div>

          <div id="dashboard-task-cards">
            <DashboardTasksPanel />
          </div>
        </div>
      </section>
    </AmbientWorkspace>
  );
}
