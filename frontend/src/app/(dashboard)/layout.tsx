'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useRef } from 'react';
import MainNav from "@/components/MainNav";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import ServiceStatusAlerts from "@/components/ServiceStatusAlerts";
import TelemetryNotice from "@/components/TelemetryNotice";
import UpdateNotice from "@/components/UpdateNotice";
import TourGuide from "@/components/TourGuide";
import CaptureShell from "@/components/CaptureShell";
import DemoBanner, { DEMO_BANNER_STYLE } from "@/components/DemoBanner";
import RecordingFloatingBadge from "@/components/RecordingFloatingBadge";
import ImportAudioHost from "@/components/importAudio/ImportAudioHost";
import StoredNotesSender from "@/components/notes/StoredNotesSender";
import { CaptureProvider } from "@/lib/capture/CaptureProvider";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const pathname = usePathname();
  const mainRef = useRef<HTMLElement | null>(null);
  const isSettingsPage = pathname?.startsWith('/settings');
  const isPeoplePage = pathname?.startsWith('/people');
  const showSidebar = pathname?.startsWith('/recordings');

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      mainRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);

  return (
    <CaptureProvider>
      <CaptureShell>
        {/* App shell: a column so the mobile top bar reserves the top row, with
            the nav <-> main row filling the rest. `h-dvh` (dynamic viewport)
            keeps the shell inside the visible area so the browser toolbar never
            clips the bottom of scroll views. On desktop TopBar is hidden, so the
            column collapses to a single row. */}
        <div
          className="flex flex-col h-dvh w-full overflow-hidden"
          style={DEMO_BANNER_STYLE}
        >
          <TourGuide />
          <DemoBanner />
          <TopBar />
          <div className="flex flex-1 min-h-0 w-full overflow-hidden">
            <MainNav />
            {!isSettingsPage && !isPeoplePage && showSidebar && <Sidebar />}

            <main
              ref={mainRef}
              className="flex-1 overflow-y-auto relative flex flex-col min-w-0 h-full"
            >
              {children}
              <TelemetryNotice />
              <UpdateNotice />
              <ServiceStatusAlerts />
            </main>
          </div>
        </div>
        <RecordingFloatingBadge />
        <ImportAudioHost />
        <StoredNotesSender />
      </CaptureShell>
    </CaptureProvider>
  );
}
