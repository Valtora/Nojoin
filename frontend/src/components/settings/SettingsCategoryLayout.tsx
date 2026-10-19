"use client";

import Link from "next/link";
import { useEffect, type ReactNode } from "react";
import { ChevronLeft } from "lucide-react";

import { cn } from "@/lib/cn";

import {
  SETTINGS_CATEGORIES,
  SETTINGS_ROOT,
  type SettingsCategoryId,
} from "./settingsCategories";

interface SettingsCategoryLayoutProps {
  categoryId: SettingsCategoryId;
  children: ReactNode;
}

/**
 * The frame every category page renders into: a back link on mobile, the
 * category heading, and the single column its cards share.
 *
 * Width is decided here, once per page, rather than per card: cards cannot opt
 * into their own width, so every card on a page lines up on both sides.
 * Data-heavy pages declare `fullBleed` on the category instead, so a page is
 * either a form or a data page.
 */
export default function SettingsCategoryLayout({
  categoryId,
  children,
}: SettingsCategoryLayoutProps) {
  const category = SETTINGS_CATEGORIES[categoryId];

  // Bring a search result, or an in-page link's target, into view. The browser
  // will not do this itself when the target is rendered after navigation, and
  // an Advanced block opened by the same hash renders a frame later, so this
  // waits two frames rather than running on mount.
  useEffect(() => {
    let frame = 0;

    const reveal = () => {
      const hash = window.location.hash.slice(1);
      if (!hash) {
        return;
      }

      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = window.requestAnimationFrame(() => {
          const target = document.getElementById(hash);
          if (!target) {
            return;
          }

          // Centre a row; align a whole card that is taller than most of
          // the window to its top, so it does not open on its middle.
          const tall =
            target.getBoundingClientRect().height > window.innerHeight * 0.6;
          target.scrollIntoView({
            block: tall ? "start" : "center",
            behavior: "smooth",
          });
          // Restart the highlight when the same target is revealed twice.
          target.classList.remove("settings-target");
          void target.offsetWidth;
          target.classList.add("settings-target");
        });
      });
    };

    reveal();
    window.addEventListener("hashchange", reveal);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("hashchange", reveal);
    };
  }, [categoryId]);

  return (
    <div className="p-4 md:p-6 lg:p-8">
      <Link
        href={SETTINGS_ROOT}
        className="mb-4 -ml-1 inline-flex items-center gap-1 rounded-lg px-1 py-1 text-sm font-medium contrast-helper transition-colors hover:text-action-text lg:hidden"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
        Settings
      </Link>

      <div
        className={cn(
          "space-y-4",
          !category.fullBleed && "mx-auto max-w-3xl",
        )}
      >
        <div>
          <h1 className="text-xl font-semibold text-foreground">
            {category.label}
          </h1>
          <p className="mt-1 text-sm leading-6 contrast-helper">
            {category.description}
          </p>
        </div>

        {children}
      </div>
    </div>
  );
}
