import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";

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
      </main>
    </div>
  );
}
