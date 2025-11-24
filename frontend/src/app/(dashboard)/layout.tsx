import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import ChatPanel from "@/components/ChatPanel";
import CompanionStatusAlert from "@/components/CompanionStatusAlert";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex h-screen w-full">
      <Sidebar recordings={[]} />
      
      <main className="flex-1 overflow-y-auto relative flex flex-col min-w-0">
        <TopBar />
        {children}
        <CompanionStatusAlert />
      </main>
      
      <ChatPanel />
    </div>
  );
}
