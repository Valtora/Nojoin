import type { ReactNode } from "react";

interface SettingsLayoutProps {
  title: string;
  description: string;
  headerAccessory?: ReactNode;
  sidebarHeader?: ReactNode;
  navigation: ReactNode;
  sidebarFooter?: ReactNode;
  children: ReactNode;
}

export default function SettingsLayout({
  title,
  description,
  headerAccessory,
  sidebarHeader,
  navigation,
  sidebarFooter,
  children,
}: SettingsLayoutProps) {
  return (
    <div className="flex h-full flex-col bg-gray-50 dark:bg-gray-900">
      <div className="flex shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 py-4 pl-14 md:px-8 md:py-6 dark:border-gray-700 dark:bg-gray-800">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            {title}
          </h1>
          <p className="mt-1 text-sm contrast-helper">{description}</p>
        </div>
        {headerAccessory}
      </div>

      <div className="flex flex-1 flex-col overflow-hidden md:flex-row">
        <aside className="flex w-full shrink-0 flex-col border-b border-r-0 bg-gray-100 md:w-64 md:border-r md:border-b-0 dark:bg-gray-900/80 contrast-border">
          {sidebarHeader && <div className="border-b p-4 contrast-border">{sidebarHeader}</div>}
          {navigation}
          {sidebarFooter && <div className="border-t p-4 contrast-border">{sidebarFooter}</div>}
        </aside>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-8">{children}</div>
      </div>
    </div>
  );
}