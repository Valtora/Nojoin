'use client';

import { Menu } from "lucide-react";
import Image from "next/image";
import { useNavigationStore } from "@/lib/store";
import { usePathname } from "next/navigation";

export default function TopBar() {
  const { toggleMobileNav } = useNavigationStore();
  const pathname = usePathname();

  // The recording detail view renders its own full-width header/controls, so it
  // supplies its own menu affordance and the shared bar would be redundant.
  if (pathname?.startsWith("/recordings/")) {
    return null;
  }

  return (
    <header className="lg:hidden shrink-0 z-30 flex items-center gap-3 border-b border-gray-200/70 bg-white/85 px-3 pt-[calc(env(safe-area-inset-top)+0.5rem)] pb-2 backdrop-blur-md dark:border-gray-800/80 dark:bg-[#0a0f1c]/90">
      <button
        onClick={toggleMobileNav}
        className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-gray-200 bg-white/90 text-gray-700 shadow-sm transition-colors hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-200 dark:hover:bg-gray-800"
        title="Open Menu"
        aria-label="Open navigation menu"
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex min-w-0 items-center gap-2">
        <Image
          src="/assets/NojoinLogo.png"
          alt=""
          width={28}
          height={28}
          className="h-7 w-7 shrink-0 object-contain"
        />
        <span className="truncate text-lg font-semibold text-orange-600">
          Nojoin
        </span>
      </div>
    </header>
  );
}
