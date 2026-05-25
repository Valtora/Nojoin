'use client';

import { Menu } from "lucide-react";
import { useNavigationStore } from "@/lib/store";
import { usePathname } from "next/navigation";

export default function TopBar() {
  const { toggleMobileNav } = useNavigationStore();
  const pathname = usePathname();

  const isRecordingView = pathname?.startsWith("/recordings/");
  const buttonClassName = "inline-flex h-10 w-10 items-center justify-center rounded-xl border border-gray-200 bg-white/90 text-gray-700 shadow-sm backdrop-blur-sm transition-colors hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-800";

  if (isRecordingView) {
    return null;
  }

  return (
    <div className="sticky top-0 z-30 border-b border-orange-100/80 bg-white/85 backdrop-blur dark:border-gray-800/80 dark:bg-gray-950/85 md:hidden">
      <div className="flex items-center px-4 py-3">
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
