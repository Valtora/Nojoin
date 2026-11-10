"use client";

import { useId } from "react";
import Link from "next/link";

import { InputBase } from "@/components/ui/nativeBases";
import type { UserTask } from "@/types";

export default function RecordingTasks({ tasks, busyTaskId, onToggle }: {
  tasks: UserTask[];
  busyTaskId: number | null;
  onToggle: (task: UserTask) => Promise<void>;
}) {
  const idPrefix = useId();
  return (
    <div className="h-full overflow-y-auto p-3 pb-clear-floating-3">
      <ul aria-label="Linked tasks" className="space-y-2">
        {tasks.map((task) => {
          // An archived task is read-only here, as the Tasks page offers it no
          // Complete or Reopen; its box still shows whether it was done.
          const archivedId = task.archived_at ? `${idPrefix}-archived-${task.id}` : undefined;
          return (
            <li key={task.id} className="flex items-start gap-3 rounded-lg p-2">
              <InputBase
                type="checkbox"
                checked={Boolean(task.completed_at)}
                // The box being saved stays enabled: disabling it would drop
                // keyboard focus to the page. The hook ignores a second toggle.
                disabled={Boolean(archivedId) || (busyTaskId !== null && busyTaskId !== task.id)}
                aria-busy={busyTaskId === task.id}
                aria-describedby={archivedId}
                aria-label={`Mark ${task.title} ${task.completed_at ? "incomplete" : "complete"}`}
                onChange={() => { if (!archivedId) void onToggle(task); }}
                className={`mt-1 h-4 w-4 shrink-0 accent-action${archivedId ? " cursor-not-allowed" : ""}`}
              />
              <div className="min-w-0 text-sm">
                <Link href={`/tasks?task=${task.id}`} title="Open in Tasks"
                  className={`break-words underline-offset-2 hover:underline ${task.completed_at ? "text-contrast-helper line-through" : "text-foreground"}`}>
                  {task.title}
                </Link>
                {archivedId && <p id={archivedId} className="text-xs text-contrast-helper">Archived</p>}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
