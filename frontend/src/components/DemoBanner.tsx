import type { CSSProperties } from "react";

import { DEMO_GET_NOJOIN_URL, DEMO_MODE } from "@/lib/demo";

/**
 * Tells the shell how tall the banner is, so MainNav's full-height column
 * leaves room for it. Undefined on a normal build, where the variable is unset
 * and MainNav is exactly the viewport's height.
 */
export const DEMO_BANNER_STYLE: CSSProperties | undefined = DEMO_MODE
  ? ({ "--demo-banner-h": "2.25rem" } as CSSProperties)
  : undefined;

/**
 * A strip across the top of the app on the public demo, so a visitor knows why
 * nothing they change is saved. Renders nothing on a normal build.
 */
export default function DemoBanner() {
  if (!DEMO_MODE) {
    return null;
  }

  return (
    <div className="flex h-9 shrink-0 items-center justify-center gap-2 overflow-hidden whitespace-nowrap border-b border-status-info-border bg-status-info-bg px-4 text-xs text-status-info-fg sm:text-sm">
      <span className="truncate">
        A read-only demo: recording and AI are off, and changes aren&apos;t
        saved.
      </span>
      <a
        href={DEMO_GET_NOJOIN_URL}
        className="shrink-0 font-semibold underline underline-offset-2"
      >
        Get Nojoin
      </a>
    </div>
  );
}
