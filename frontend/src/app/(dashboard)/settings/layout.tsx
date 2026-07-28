import type { ReactNode } from "react";

import SettingsProvider from "@/components/settings/SettingsProvider";
import SettingsShell from "@/components/settings/SettingsShell";

/**
 * The settings frame, above every category route.
 *
 * The provider must live here rather than in the pages: it owns the debounced
 * autosave, and a hook that unmounts mid-debounce discards the pending write
 * without flushing. Because this layout survives navigation between categories,
 * an edit made a moment before switching category still lands.
 */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <SettingsProvider>
      <SettingsShell>{children}</SettingsShell>
    </SettingsProvider>
  );
}
