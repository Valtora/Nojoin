'use client';

import { Menu } from "lucide-react";
import { useNavigationStore } from "@/lib/store";
import { usePathname } from "next/navigation";

export default function TopBar() {
  const { toggleMobileNav } = useNavigationStore();
  const pathname = usePathname();

  const isRecordingView = pathname?.startsWith("/recordings/");
  const buttonClassName = "pointer-events-auto inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-gray-200 bg-white/90 text-gray-700 shadow-lg shadow-black/10 backdrop-blur-sm transition-colors hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-800 dark:shadow-black/30";

  if (isRecordingView) {
    return null;
  }

  return (
    <div className="pointer-events-none fixed left-4 top-[calc(env(safe-area-inset-top)+0.75rem)] z-40 lg:hidden">
      <div className="flex items-center">
        <button
          onClick={toggleMobileNav}
          className={buttonClassName}
          title="Open Menu"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}
