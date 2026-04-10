"use client";

import Link from "next/link";
import {
  Archive,
  ArrowRight,
  FolderOpen,
  Search,
  Tags,
  Trash2,
} from "lucide-react";

import { useNavigationStore } from "@/lib/store";

import AmbientWorkspace from "./AmbientWorkspace";

const viewMeta = {
  recordings: {
    eyebrow: "Recordings workspace",
    title: "Choose a meeting from the library.",
    description:
      "Use the list on the left to open a recording, search across titles and speakers, or start a fresh capture from the sidebar controls.",
    icon: FolderOpen,
  },
  archived: {
    eyebrow: "Archive",
    title: "Review meetings you have set aside.",
    description:
      "Archived recordings stay out of the active workspace while remaining easy to recover when you need them again.",
    icon: Archive,
  },
  deleted: {
    eyebrow: "Trash",
    title: "Manage recordings queued for removal.",
    description:
      "Restore a meeting back into the workspace or permanently delete it once you are certain it is no longer needed.",
    icon: Trash2,
  },
} as const;

export default function RecordingsHome() {
  const { currentView, selectedTagIds } = useNavigationStore();
  const meta = viewMeta[currentView];
  const Icon = meta.icon;

  return (
    <AmbientWorkspace contentClassName="max-w-5xl gap-6">
      <section className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-2xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20 md:p-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl space-y-4">
            <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
              <Icon className="h-3.5 w-3.5" />
              {meta.eyebrow}
            </span>

            <div className="space-y-3">
              <h1 className="text-3xl font-semibold tracking-tight text-gray-950 dark:text-white md:text-5xl">
                {meta.title}
              </h1>
              <p className="text-sm leading-6 text-gray-600 dark:text-gray-300 md:text-base">
                {meta.description}
              </p>
            </div>
          </div>

          <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60 lg:max-w-xs">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Current scope
            </div>
            <div className="mt-3 text-2xl font-semibold text-gray-950 dark:text-white">
              {currentView === "recordings"
                ? "Active library"
                : currentView === "archived"
                  ? "Archived meetings"
                  : "Deleted meetings"}
            </div>
            <div className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              {selectedTagIds.length > 0
                ? `${selectedTagIds.length} tag filter${selectedTagIds.length === 1 ? " is" : "s are"} applied from the sidebar.`
                : "No tag filters are active at the moment."}
            </div>
          </div>
        </div>

        <div className="mt-8 grid gap-4 md:grid-cols-3">
          <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-5 dark:border-white/10 dark:bg-gray-900/60">
            <div className="flex items-center gap-3 text-gray-900 dark:text-white">
              <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                <Search className="h-5 w-5" />
              </div>
              <span className="font-medium">Search quickly</span>
            </div>
            <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Search covers meeting titles, tags, local speaker names, and linked
              people, so you can jump back into work without hunting manually.
            </p>
          </div>

          <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-5 dark:border-white/10 dark:bg-gray-900/60">
            <div className="flex items-center gap-3 text-gray-900 dark:text-white">
              <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                <Tags className="h-5 w-5" />
              </div>
              <span className="font-medium">Filter deliberately</span>
            </div>
            <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Combine tags, date filters, and speaker filters from the sidebar to
              narrow the workspace before you open a specific meeting.
            </p>
          </div>

          <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-5 dark:border-white/10 dark:bg-gray-900/60">
            <div className="flex items-center gap-3 text-gray-900 dark:text-white">
              <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                <FolderOpen className="h-5 w-5" />
              </div>
              <span className="font-medium">Work the queue</span>
            </div>
            <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Open any meeting to review transcripts, notes, documents, exports,
              and processing status in the full editor workspace.
            </p>
          </div>
        </div>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-full bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-orange-700"
          >
            Back to dashboard
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </AmbientWorkspace>
  );
}