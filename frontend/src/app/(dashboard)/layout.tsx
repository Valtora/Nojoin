'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useRef } from 'react';
import MainNav from "@/components/MainNav";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import ServiceStatusAlerts from "@/components/ServiceStatusAlerts";
import TourGuide from "@/components/TourGuide";
import CaptureShell from "@/components/CaptureShell";
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
        <div className="flex h-screen w-full overflow-hidden">
          <TourGuide />
          <MainNav />
          {!isSettingsPage && !isPeoplePage && showSidebar && <Sidebar />}
          
          <main
            ref={mainRef}
            className="flex-1 overflow-y-auto relative flex flex-col min-w-0 h-full"
          >
            <TopBar />
            {children}
            <ServiceStatusAlerts />
          </main>
        </div>
      </CaptureShell>
    </CaptureProvider>
  );
}
