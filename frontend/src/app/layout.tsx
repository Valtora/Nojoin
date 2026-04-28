import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";
import { ThemeProvider } from "@/lib/ThemeProvider";
import { themeScript } from "@/lib/theme-script";
import NotificationToast from "@/components/NotificationToast";
import AuthGuard from "@/components/AuthGuard";
import BackupPoller from "@/components/BackupPoller";

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
  icons: {
    icon: '/favicon.ico',
  },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-white dark:bg-black text-gray-900 dark:text-gray-100 overflow-hidden`}
      >
        <ThemeProvider>
          <AuthGuard>
            {children}
          </AuthGuard>
          <NotificationToast />
          <BackupPoller />
        </ThemeProvider>
      </body>
    </html>
  );
}
