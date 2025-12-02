'use client';

import { usePathname } from 'next/navigation';
import MainNav from "@/components/MainNav";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import ChatPanel from "@/components/ChatPanel";
import ServiceStatusAlerts from "@/components/ServiceStatusAlerts";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const pathname = usePathname();
  const isSettingsPage = pathname?.startsWith('/settings');

  return (
    <div className="flex h-screen w-full">
      <MainNav />
      {!isSettingsPage && <Sidebar />}
      
      <main className="flex-1 overflow-y-auto relative flex flex-col min-w-0">
        <TopBar />
        {children}
        <ServiceStatusAlerts />
      </main>
    </div>
  );
}
