'use client';

import { Menu } from "lucide-react";
import Image from "next/image";
import { useNavigationStore } from "@/lib/store";
import { usePathname } from "next/navigation";

import IconButton from "./ui/IconButton";

export default function TopBar() {
  const { toggleMobileNav } = useNavigationStore();
  const pathname = usePathname();

  // The recording detail view renders its own full-width header/controls, so it
  // supplies its own menu affordance and the shared bar would be redundant.
  if (pathname?.startsWith("/recordings/")) {
    return null;
  }

  return (
    <header className="z-[var(--z-sticky)] flex shrink-0 items-center gap-3 border-b border-rail-border bg-rail px-3 pt-[calc(env(safe-area-inset-top)+0.5rem)] pb-2 lg:hidden">
      <IconButton
        onClick={toggleMobileNav}
        variant="secondary"
        icon={<Menu aria-hidden="true" />}
        title="Open Menu"
        aria-label="Open navigation menu"
      />
      <div className="flex min-w-0 items-center gap-2">
        <Image
          src="/assets/NojoinLogo.png"
          alt=""
          width={28}
          height={28}
          className="h-7 w-7 shrink-0 object-contain"
        />
        <span className="truncate text-lg font-semibold text-action-text">
          Nojoin
        </span>
      </div>
    </header>
  );
}
