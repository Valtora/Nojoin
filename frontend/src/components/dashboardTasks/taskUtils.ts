import { formatTimeZoneDate } from "@/lib/timezone";
import { UserTask } from "@/types";

export const DAY_IN_MS = 24 * 60 * 60 * 1000;
export const HOUR_IN_MS = 60 * 60 * 1000;

export const DEADLINE_TRIGGER_CLASS =
  "inline-flex h-8 max-w-full items-center gap-2 rounded-full border border-dashed border-gray-300 bg-white/90 px-3 py-1 text-xs font-medium text-gray-700 shadow-none transition-colors hover:border-orange-300 hover:text-orange-700 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 dark:border-gray-600 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-200 dark:focus:ring-offset-gray-950";

export function parseTaskDeadline(value: string): Date | null {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function sortTasks(tasks: UserTask[]): UserTask[] {
  const active = tasks
    .filter((task) => !task.archived_at && !task.completed_at)
    .sort((left, right) => {
      const leftDue = left.due_at
        ? parseTaskDeadline(left.due_at)?.getTime() ?? Number.MAX_SAFE_INTEGER
        : Number.MAX_SAFE_INTEGER;
      const rightDue = right.due_at
        ? parseTaskDeadline(right.due_at)?.getTime() ?? Number.MAX_SAFE_INTEGER
        : Number.MAX_SAFE_INTEGER;

      if (leftDue !== rightDue) {
        return leftDue - rightDue;
      }

      return (
        new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
      );
    });

  const completed = tasks
    .filter((task) => !task.archived_at && Boolean(task.completed_at))
    .sort(
      (left, right) =>
        new Date(right.completed_at || 0).getTime() -
        new Date(left.completed_at || 0).getTime(),
    );

  return [...active, ...completed];
}

export function getTimeRemainingState(
  task: UserTask,
  now: Date,
): {
  label: string;
  className: string;
} | null {
  if (!task.due_at || task.completed_at) {
    return null;
  }

  const dueDate = parseTaskDeadline(task.due_at);
  if (!dueDate) {
    return null;
  }

  const deltaMs = dueDate.getTime() - now.getTime();

  if (deltaMs < 0) {
    const overdueMs = Math.abs(deltaMs);
    const overdueDays = Math.floor(overdueMs / DAY_IN_MS);
    const overdueHours = Math.floor(overdueMs / HOUR_IN_MS);

    return {
      label:
        overdueDays >= 1
          ? `Overdue by ${overdueDays}d`
          : overdueHours >= 1
            ? `Overdue by ${overdueHours}${overdueHours === 1 ? "hr" : "hrs"}`
            : "Overdue",
      className:
        "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300",
    };
  }

  if (deltaMs >= DAY_IN_MS) {
    const daysRemaining = Math.floor(deltaMs / DAY_IN_MS);

    return {
      label: `Due in ${daysRemaining}d`,
      className:
        "border-gray-300 bg-gray-100 text-gray-800 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200",
    };
  }

  const hoursRemaining = Math.floor(deltaMs / HOUR_IN_MS);

  return {
    label:
      hoursRemaining >= 1
        ? `Due in ${hoursRemaining}${hoursRemaining === 1 ? "hr" : "hrs"}`
        : "Due in <1h",
    className:
      "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300",
  };
}

export function getDeadlineTriggerLabel(
  deadline: Date | null,
  timeZone: string,
): string {
  if (!deadline) {
    return "Add deadline";
  }

  return formatTimeZoneDate(deadline, timeZone, "EEE d MMM, h:mm aa");
}
