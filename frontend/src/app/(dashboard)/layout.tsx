'use client';

import { usePathname } from 'next/navigation';
import MainNav from "@/components/MainNav";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import ServiceStatusAlerts from "@/components/ServiceStatusAlerts";
import TourGuide from "@/components/TourGuide";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const pathname = usePathname();
  const isSettingsPage = pathname?.startsWith('/settings');
  const isPeoplePage = pathname?.startsWith('/people');

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <TourGuide />
      <MainNav />
      {!isSettingsPage && !isPeoplePage && <Sidebar />}
      
      <main className="flex-1 overflow-y-auto relative flex flex-col min-w-0 h-full">
        <TopBar />
        {children}
        <ServiceStatusAlerts />
      </main>
    </div>
  );
}
