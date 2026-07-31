"use client";

import Link from "next/link";
import { LifeBuoy, Waves } from "lucide-react";

import Workspace from "./Workspace";
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
    <Workspace
      contentClassName="workspace-shell workspace-shell-feature"
      paddingClassName="workspace-pad-y"
    >
      <section
        id="recordings-landing-panel"
        className="density-surface density-surface-lg border border-surface-border bg-surface-card shadow-card"
      >
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-action-border bg-action-tint px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-action-text">
            <Waves className="h-3.5 w-3.5" />
            Recordings Workspace
          </div>

          <h1 className="density-heading-page mt-4 text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            {copy.title}
          </h1>

          <div className="density-body-copy mt-6 space-y-3 text-base leading-7 text-contrast-helper">
            <p>{copy.description}</p>
            <p>{copy.detail}</p>
          </div>

          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/settings/help"
              className="inline-flex items-center gap-2 rounded-xl border border-surface-border bg-surface-card px-4 py-3 text-sm font-semibold text-foreground transition-colors hover:border-action-border hover:text-action-text"
            >
              <LifeBuoy className="h-4 w-4" />
              Help
            </Link>
          </div>
        </div>
      </section>
    </Workspace>
  );
}
