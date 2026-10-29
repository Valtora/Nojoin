"use client";

import Link from "next/link";

import { InputBase } from "@/components/ui/nativeBases";
import type { UserTask } from "@/types";

export default function RecordingTasks({ tasks, busyTaskId, onToggle }: {
  tasks: UserTask[];
  busyTaskId: number | null;
  onToggle: (task: UserTask) => Promise<void>;
}) {
  return (
    <div className="h-full overflow-y-auto p-3 pb-clear-floating-3">
      <ul aria-label="Linked tasks" className="space-y-2">
        {tasks.map((task) => (
          <li key={task.id} className="flex items-start gap-3 rounded-lg p-2">
            <InputBase
              type="checkbox"
              checked={Boolean(task.completed_at)}
              disabled={busyTaskId !== null}
              aria-label={`Mark ${task.title} ${task.completed_at ? "incomplete" : "complete"}`}
              onChange={() => void onToggle(task)}
              className="mt-1 h-4 w-4 shrink-0 accent-action"
            />
            <div className="min-w-0 text-sm">
              <Link href={`/tasks?task=${task.id}`} title="Open in Tasks"
                className={`break-words underline-offset-2 hover:underline ${task.completed_at ? "text-contrast-helper line-through" : "text-foreground"}`}>
                {task.title}
              </Link>
              {task.archived_at && <p className="text-xs text-contrast-helper">Archived</p>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
