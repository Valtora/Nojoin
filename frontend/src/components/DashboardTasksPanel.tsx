"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { format, isBefore, isToday, startOfToday } from "date-fns";
import { Check, Loader2, Trash2 } from "lucide-react";

import {
  createUserTask,
  deleteUserTask,
  getUserTasks,
  updateUserTask,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { UserTask } from "@/types";

import ModernDatePicker from "./ui/ModernDatePicker";

function parseDateOnly(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function toDateOnlyString(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function sortTasks(tasks: UserTask[]): UserTask[] {
  const active = tasks
    .filter((task) => !task.completed_at)
    .sort((left, right) => {
      const leftDue = left.due_on
        ? parseDateOnly(left.due_on).getTime()
        : Number.MAX_SAFE_INTEGER;
      const rightDue = right.due_on
        ? parseDateOnly(right.due_on).getTime()
        : Number.MAX_SAFE_INTEGER;

      if (leftDue !== rightDue) {
        return leftDue - rightDue;
      }

      return (
        new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
      );
    });

  const completed = tasks
    .filter((task) => Boolean(task.completed_at))
    .sort(
      (left, right) =>
        new Date(right.completed_at || 0).getTime() -
        new Date(left.completed_at || 0).getTime(),
    );

  return [...active, ...completed];
}

function getDueState(task: UserTask): {
  label: string;
  className: string;
} | null {
  if (!task.due_on) {
    return null;
  }

  const dueDate = parseDateOnly(task.due_on);
  if (isToday(dueDate)) {
    return {
      label: "Due today",
      className:
        "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300",
    };
  }

  if (!task.completed_at && isBefore(dueDate, startOfToday())) {
    return {
      label: `Overdue since ${format(dueDate, "d MMM")}`,
      className:
        "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300",
    };
  }

  return {
    label: `Due ${format(dueDate, "EEE d MMM")}`,
    className:
      "border-gray-200 bg-gray-50 text-gray-700 dark:border-white/10 dark:bg-gray-900/70 dark:text-gray-300",
  };
}

export default function DashboardTasksPanel() {
  const [tasks, setTasks] = useState<UserTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    let cancelled = false;

    const loadTasks = async () => {
      try {
        const data = await getUserTasks();
        if (!cancelled) {
          setTasks(sortTasks(data));
        }
      } catch (loadError: any) {
        if (!cancelled) {
          setError(loadError.response?.data?.detail || "Failed to load tasks.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadTasks();

    return () => {
      cancelled = true;
    };
  }, []);

  const sortedTasks = useMemo(() => sortTasks(tasks), [tasks]);
  const openTasks = useMemo(
    () => sortedTasks.filter((task) => !task.completed_at),
    [sortedTasks],
  );
  const completedTasks = useMemo(
    () => sortedTasks.filter((task) => Boolean(task.completed_at)),
    [sortedTasks],
  );

  useEffect(() => {
    if (isComposerOpen) {
      titleInputRef.current?.focus();
    }
  }, [isComposerOpen]);

  const handleCloseComposer = () => {
    setIsComposerOpen(false);
    setTitle("");
    setError(null);
  };

  const handleCreateTask = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError("Enter a task title before adding it.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const createdTask = await createUserTask({
        title: trimmedTitle,
      });

      setTasks((currentTasks) => sortTasks([...currentTasks, createdTask]));
      setTitle("");
      setIsComposerOpen(false);
      addNotification({ message: "Task added.", type: "success" });
    } catch (createError: any) {
      setError(createError.response?.data?.detail || "Failed to add task.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleComposerKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
  ) => {
    if (event.key === "Escape") {
      event.preventDefault();
      handleCloseComposer();
    }
  };

  const handleToggleTask = async (task: UserTask) => {
    setBusyTaskId(task.id);
    setError(null);

    try {
      const updatedTask = await updateUserTask(task.id, {
        completed: !task.completed_at,
      });

      setTasks((currentTasks) =>
        sortTasks(
          currentTasks.map((currentTask) =>
            currentTask.id === updatedTask.id ? updatedTask : currentTask,
          ),
        ),
      );
    } catch (toggleError: any) {
      addNotification({
        message:
          toggleError.response?.data?.detail || "Failed to update task state.",
        type: "error",
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleDeleteTask = async (taskId: number) => {
    setBusyTaskId(taskId);
    setError(null);

    try {
      await deleteUserTask(taskId);
      setTasks((currentTasks) =>
        currentTasks.filter((currentTask) => currentTask.id !== taskId),
      );
      addNotification({ message: "Task removed.", type: "success" });
    } catch (deleteError: any) {
      addNotification({
        message:
          deleteError.response?.data?.detail || "Failed to delete task.",
        type: "error",
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleAssignDeadline = async (taskId: number, dueDate: Date | null) => {
    if (!dueDate) {
      return;
    }

    setBusyTaskId(taskId);
    setError(null);

    try {
      const updatedTask = await updateUserTask(taskId, {
        due_on: toDateOnlyString(dueDate),
      });

      setTasks((currentTasks) =>
        sortTasks(
          currentTasks.map((currentTask) =>
            currentTask.id === updatedTask.id ? updatedTask : currentTask,
          ),
        ),
      );
      addNotification({ message: "Deadline added.", type: "success" });
    } catch (deadlineError: any) {
      addNotification({
        message:
          deadlineError.response?.data?.detail || "Failed to save deadline.",
        type: "error",
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  return (
    <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold text-gray-950 dark:text-white">
          To-Do List
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
            {openTasks.length} open
          </span>
          {completedTasks.length > 0 && (
            <span className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-3 py-1 text-xs font-semibold text-gray-600 dark:border-white/10 dark:bg-white/5 dark:text-gray-300">
              {completedTasks.length} completed
            </span>
          )}
        </div>
      </div>

      {isComposerOpen ? (
        <form
          onSubmit={handleCreateTask}
          className="relative mt-6"
        >
          <input
            ref={titleInputRef}
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="Type a task and press Enter"
            disabled={submitting}
            className="h-12 w-full border-0 border-b border-orange-200 bg-transparent px-1 pr-10 text-base text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 dark:border-orange-500/20 dark:text-gray-100 dark:placeholder:text-gray-500"
          />

          {submitting && (
            <Loader2 className="pointer-events-none absolute right-1 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-orange-600 dark:text-orange-300" />
          )}
        </form>
      ) : (
        <button
          type="button"
          onClick={() => {
            setError(null);
            setIsComposerOpen(true);
          }}
          className="mt-6 w-full border-0 border-b border-white/10 px-1 py-3 text-left text-sm text-gray-400 transition-colors hover:border-orange-300 hover:text-gray-500 dark:text-gray-500 dark:hover:border-orange-500/30 dark:hover:text-gray-300"
        >
          Write a task...
        </button>
      )}

      {error && (
        <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <div className="mt-6 space-y-6">
        {loading ? (
          <div className="flex items-center gap-3 rounded-[1.5rem] border border-white/60 bg-white/70 px-4 py-4 text-sm text-gray-600 dark:border-white/10 dark:bg-gray-900/60 dark:text-gray-300">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading your tasks...
          </div>
        ) : (
          <>
            {openTasks.length > 0 && (
              <div className="space-y-3">
                {openTasks.map((task) => {
                  const dueState = getDueState(task);
                  const isBusy = busyTaskId === task.id;

                  return (
                    <div
                      key={task.id}
                      className="group flex items-start gap-4 rounded-[1.75rem] border border-white/70 bg-gradient-to-br from-white via-white to-orange-50/50 px-4 py-4 shadow-sm shadow-orange-950/5 transition-all hover:-translate-y-0.5 hover:shadow-lg dark:border-white/10 dark:from-gray-900/80 dark:via-gray-900/70 dark:to-orange-500/10"
                    >
                      <button
                        type="button"
                        onClick={() => void handleToggleTask(task)}
                        disabled={isBusy}
                        className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white text-transparent transition-colors hover:border-orange-400 hover:bg-orange-50 hover:text-orange-600 dark:border-white/10 dark:bg-gray-950/60 dark:hover:border-orange-500/30 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
                        aria-label={`Mark ${task.title} complete`}
                      >
                        {isBusy ? (
                          <Loader2 className="h-4 w-4 animate-spin text-orange-600 dark:text-orange-300" />
                        ) : (
                          <Check className="h-4 w-4" />
                        )}
                      </button>

                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-gray-900 dark:text-white">
                          {task.title}
                        </p>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          {dueState && (
                            <span
                              className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${dueState.className}`}
                            >
                              {dueState.label}
                            </span>
                          )}
                          {!dueState && (
                            <ModernDatePicker
                              selected={null}
                              onChange={(date) =>
                                void handleAssignDeadline(task.id, date)
                              }
                              dateFormat="dd MMM yyyy"
                              placeholderText="Add deadline"
                              disabled={isBusy}
                              className="w-auto"
                              inputClassName="h-8 rounded-full border-dashed border-gray-200 bg-white/80 px-3 py-1 text-xs font-medium text-gray-500 shadow-none dark:border-white/10 dark:bg-white/5 dark:text-gray-300"
                            />
                          )}
                          <span className="inline-flex rounded-full bg-gray-950/[0.04] px-2.5 py-1 text-xs font-medium text-gray-500 dark:bg-white/5 dark:text-gray-400">
                            Added {format(new Date(task.created_at), "d MMM")}
                          </span>
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => void handleDeleteTask(task.id)}
                        disabled={isBusy}
                        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
                        aria-label={`Delete ${task.title}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {completedTasks.length > 0 && (
              <div
                className={`space-y-3 ${
                  openTasks.length > 0
                    ? "border-t border-gray-200 pt-6 dark:border-white/10"
                    : ""
                }`}
              >
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                  Completed
                </div>
                {completedTasks.map((task) => {
                  const isBusy = busyTaskId === task.id;

                  return (
                    <div
                      key={task.id}
                      className="flex items-start gap-4 rounded-[1.75rem] border border-white/60 bg-white/60 px-4 py-4 dark:border-white/10 dark:bg-gray-900/50"
                    >
                      <button
                        type="button"
                        onClick={() => void handleToggleTask(task)}
                        disabled={isBusy}
                        className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-emerald-200 bg-emerald-100 text-emerald-700 transition-colors hover:border-emerald-300 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                        aria-label={`Mark ${task.title} incomplete`}
                      >
                        {isBusy ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Check className="h-4 w-4" />
                        )}
                      </button>

                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-gray-500 line-through dark:text-gray-400">
                          {task.title}
                        </p>
                        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                          {task.completed_at && (
                            <span className="inline-flex rounded-full bg-gray-950/[0.04] px-2.5 py-1 dark:bg-white/5">
                              Completed {format(new Date(task.completed_at), "d MMM")}
                            </span>
                          )}
                          {task.due_on && (
                            <span className="inline-flex rounded-full bg-gray-950/[0.04] px-2.5 py-1 dark:bg-white/5">
                              Deadline {format(parseDateOnly(task.due_on), "d MMM")}
                            </span>
                          )}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => void handleDeleteTask(task.id)}
                        disabled={isBusy}
                        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
                        aria-label={`Delete ${task.title}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
