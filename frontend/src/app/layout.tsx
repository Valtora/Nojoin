import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import ChatPanel from "@/components/ChatPanel";
import CompanionStatusAlert from "@/components/CompanionStatusAlert";
import TopBar from "@/components/TopBar";
import { getRecordings } from "@/lib/api";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Nojoin",
  description: "Self-hosted meeting intelligence platform",
};

export const dynamic = 'force-dynamic';

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  let recordings = [];
  try {
    recordings = await getRecordings();
  } catch (e) {
    console.error("Failed to fetch recordings in layout:", e);
  }

  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-white dark:bg-black text-gray-900 dark:text-gray-100 overflow-hidden`}
      >
        <div className="flex h-screen w-full">
          <Sidebar recordings={recordings} />
          
          <main className="flex-1 overflow-y-auto relative flex flex-col min-w-0">
            <TopBar />
            {children}
          </main>

          <ChatPanel />
          <CompanionStatusAlert />
        </div>
      </body>
    </html>
  );
}
