import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import "./fonts/geist";
import "./globals.css";
import { ThemeProvider } from "@/lib/ThemeProvider";
import { themeScript } from "@/lib/theme-script";
import NotificationToast from "@/components/NotificationToast";
import AuthGuard from "@/components/AuthGuard";
import BackupPoller from "@/components/BackupPoller";
import RestorePoller from "@/components/RestorePoller";
import { ViewportDensityProvider } from "@/components/ViewportDensityProvider";

export const metadata: Metadata = {
  title: "Nojoin",
  description: "Self-hosted meeting intelligence platform",
  icons: {
    icon: '/favicon.ico',
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
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
        className="antialiased bg-background text-foreground overflow-hidden"
      >
        <ThemeProvider>
          <ViewportDensityProvider>
            <AuthGuard>
              {children}
            </AuthGuard>
            <NotificationToast />
            <BackupPoller />
            <RestorePoller />
          </ViewportDensityProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
