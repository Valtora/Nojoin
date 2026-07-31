import { formatTimeZoneDate } from "@/lib/timezone";
import { UserTask } from "@/types";

export const DAY_IN_MS = 24 * 60 * 60 * 1000;
export const HOUR_IN_MS = 60 * 60 * 1000;

export const DEADLINE_TRIGGER_CLASS =
  "inline-flex h-8 max-w-full items-center gap-2 rounded-full border border-dashed border-contrast-border bg-surface-card px-3 py-1 text-xs font-medium text-contrast-muted transition-colors duration-150 hover:border-action-border hover:text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";

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
        "border-status-danger-border bg-status-danger-bg text-status-danger-fg",
    };
  }

  if (deltaMs >= DAY_IN_MS) {
    const daysRemaining = Math.floor(deltaMs / DAY_IN_MS);

    return {
      label: `Due in ${daysRemaining}d`,
      className:
        "border-status-neutral-border bg-status-neutral-bg text-status-neutral-fg",
    };
  }

  const hoursRemaining = Math.floor(deltaMs / HOUR_IN_MS);

  return {
    label:
      hoursRemaining >= 1
        ? `Due in ${hoursRemaining}${hoursRemaining === 1 ? "hr" : "hrs"}`
        : "Due in <1h",
    className:
      "border-status-warning-border bg-status-warning-bg text-status-warning-fg",
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
