'use client';

import { usePathname } from 'next/navigation';
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
  const isSettingsPage = pathname?.startsWith('/settings');
  const isPeoplePage = pathname?.startsWith('/people');
  const showSidebar = pathname?.startsWith('/recordings');

  return (
    <CaptureProvider>
      <CaptureShell>
        <div className="flex h-screen w-full overflow-hidden">
          <TourGuide />
          <MainNav />
          {!isSettingsPage && !isPeoplePage && showSidebar && <Sidebar />}
          
          <main className="flex-1 overflow-y-auto relative flex flex-col min-w-0 h-full">
            <TopBar />
            {children}
            <ServiceStatusAlerts />
          </main>
        </div>
      </CaptureShell>
    </CaptureProvider>
  );
}
