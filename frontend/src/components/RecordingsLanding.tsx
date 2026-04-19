"use client";

import Link from "next/link";
import { LifeBuoy, Waves } from "lucide-react";

import AmbientWorkspace from "./AmbientWorkspace";

export default function RecordingsLanding() {
  return (
    <AmbientWorkspace
      contentClassName="max-w-4xl gap-6"
      paddingClassName="py-6 md:py-8"
    >
      <section
        id="recordings-landing-panel"
        className="rounded-[2rem] border border-white/60 bg-white/82 p-8 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20 md:p-10"
      >
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
            <Waves className="h-3.5 w-3.5" />
            Recordings Workspace
          </div>

          <h1 className="mt-4 text-3xl font-semibold tracking-tight text-gray-950 dark:text-white md:text-4xl">
            No recordings yet.
          </h1>

          <div className="mt-6 space-y-3 text-base leading-7 text-gray-600 dark:text-gray-300">
            <p>
              Click the Start Meeting button to begin recording a meeting.
            </p>
            <p>
              If you&apos;re not sure how to use Nojoin, go to the Help page in Settings for more information.
            </p>
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