"use client";

import Link from "next/link";
import { LifeBuoy, Waves } from "lucide-react";

import AmbientWorkspace from "./AmbientWorkspace";
import { useNavigationStore } from "@/lib/store";

const LANDING_COPY = {
  recordings: {
    title: "Select a recording",
    description:
      "Choose a meeting from the list to review transcripts, notes, documents, and linked context.",
    detail:
      "If the list is empty, start a meeting or import audio to build your library.",
  },
  archived: {
    title: "Select an archived recording",
    description:
      "Choose an archived meeting from the list to review its transcript, notes, and linked files.",
    detail:
      "Use filters to narrow older sessions and restore a recording when you need it back in the main library.",
  },
  deleted: {
    title: "Select a deleted recording",
    description:
      "Choose an item from the list to inspect it before restoring or permanently deleting it.",
    detail:
      "The recordings list stays available here so review on smaller screens starts with browsing, not an automatic jump.",
  },
} as const;

export default function RecordingsLanding() {
  const currentView = useNavigationStore((state) => state.currentView);
  const copy = LANDING_COPY[currentView];

  return (
    <AmbientWorkspace
      contentClassName="workspace-shell workspace-shell-feature"
      paddingClassName="workspace-pad-y"
    >
      <section
        id="recordings-landing-panel"
        className="density-surface density-surface-lg border border-white/60 bg-white/82 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20"
      >
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
            <Waves className="h-3.5 w-3.5" />
            Recordings Workspace
          </div>

          <h1 className="density-heading-page mt-4 text-3xl font-semibold tracking-tight text-gray-950 dark:text-white md:text-4xl">
            {copy.title}
          </h1>

          <div className="density-body-copy mt-6 space-y-3 text-base leading-7 text-gray-600 dark:text-gray-300">
            <p>{copy.description}</p>
            <p>{copy.detail}</p>
          </div>

          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/settings?tab=help"
              className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm font-semibold text-gray-900 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-white/10 dark:bg-gray-900/80 dark:text-white dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            >
              <LifeBuoy className="h-4 w-4" />
              Help
            </Link>
          </div>
        </div>
      </section>
    </AmbientWorkspace>
  );
}
