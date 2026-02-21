'use client';

import { Menu, ArrowLeft } from "lucide-react";
import { useNavigationStore } from "@/lib/store";
import { usePathname, useRouter } from "next/navigation";

export default function TopBar() {
  const { toggleMobileNav } = useNavigationStore();
  const pathname = usePathname();
  const router = useRouter();

  const isRecordingView = pathname?.startsWith("/recordings/");

  return (
    <div className="fixed top-4 left-4 z-60 flex gap-2 md:hidden">
      {isRecordingView ? (
        <button
          onClick={() => router.push("/")}
          className="p-2 bg-white/80 dark:bg-gray-800/80 backdrop-blur-sm border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 rounded-lg shadow-sm hover:bg-white dark:hover:bg-gray-800 transition-colors"
          title="Back to Recordings"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
      ) : (
        <button
          onClick={toggleMobileNav}
          className="p-2 bg-white/80 dark:bg-gray-800/80 backdrop-blur-sm border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 rounded-lg shadow-sm hover:bg-white dark:hover:bg-gray-800 transition-colors"
          title="Open Menu"
        >
          <Menu className="w-5 h-5" />
        </button>
      )}
    </div>
  );
}
