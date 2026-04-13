"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2, Trash2 } from "lucide-react";

import {
  createUserTask,
  deleteUserTask,
  getUserTasks,
  updateUserTask,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { UserTask } from "@/types";

import TaskDeadlinePicker from "./ui/TaskDeadlinePicker";

const DAY_IN_MS = 24 * 60 * 60 * 1000;
const HOUR_IN_MS = 60 * 60 * 1000;
const DEADLINE_INPUT_CLASS =
  "h-8 rounded-full border-dashed border-gray-300 bg-white/90 px-3 py-1 text-xs font-medium text-gray-700 shadow-none dark:border-gray-600 dark:bg-gray-900/80 dark:text-gray-200";

function parseTaskDeadline(value: string): Date | null {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function toLocalDateTimeString(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  const hours = String(value.getHours()).padStart(2, "0");
  const minutes = String(value.getMinutes()).padStart(2, "0");
  const seconds = String(value.getSeconds()).padStart(2, "0");

  return `${year}-${month}-${day}T${hours}:${minutes}:${seconds}`;
}

function sortTasks(tasks: UserTask[]): UserTask[] {
  const active = tasks
    .filter((task) => !task.completed_at)
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
    .filter((task) => Boolean(task.completed_at))
    .sort(
      (left, right) =>
        new Date(right.completed_at || 0).getTime() -
        new Date(left.completed_at || 0).getTime(),
    );

  return [...active, ...completed];
}

function getTimeRemainingState(
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

export default function DashboardTasksPanel() {
  const [tasks, setTasks] = useState<UserTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const [savingTitleTaskId, setSavingTitleTaskId] = useState<number | null>(null);
  const [editingTaskId, setEditingTaskId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [now, setNow] = useState(() => new Date());
  const titleInputRef = useRef<HTMLInputElement>(null);
  const editingFormRef = useRef<HTMLFormElement>(null);
  const editingInputRef = useRef<HTMLInputElement>(null);
  const pendingTitleSaveRef = useRef<Promise<boolean> | null>(null);
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

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNow(new Date());
    }, 60000);

    return () => {
      window.clearInterval(interval);
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

  useEffect(() => {
    if (editingTaskId !== null) {
      editingInputRef.current?.focus();
      editingInputRef.current?.select();
    }
  }, [editingTaskId]);

  useEffect(() => {
    if (editingTaskId === null) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }

      if (editingFormRef.current?.contains(target)) {
        return;
      }

      void commitEditingTask();
    };

    document.addEventListener("pointerdown", handlePointerDown, true);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
    };
  }, [editingTaskId, editingTitle, tasks]);

  const handleCloseComposer = () => {
    setIsComposerOpen(false);
    setTitle("");
    setError(null);
  };

  const resetEditingTask = () => {
    setEditingTaskId(null);
    setEditingTitle("");
  };

  const commitEditingTask = async (): Promise<boolean> => {
    if (editingTaskId === null) {
      return true;
    }

    if (pendingTitleSaveRef.current) {
      return pendingTitleSaveRef.current;
    }

    const task = tasks.find((currentTask) => currentTask.id === editingTaskId);
    if (!task) {
      resetEditingTask();
      return true;
    }

    const trimmedTitle = editingTitle.trim();
    if (!trimmedTitle) {
      setError("Enter a task title before saving it.");
      return false;
    }

    if (trimmedTitle === task.title) {
      setError(null);
      resetEditingTask();
      return true;
    }

    setSavingTitleTaskId(task.id);
    setError(null);

    const savePromise = (async () => {
      try {
        const updatedTask = await updateUserTask(task.id, {
          title: trimmedTitle,
        });

        setTasks((currentTasks) =>
          sortTasks(
            currentTasks.map((currentTask) =>
              currentTask.id === updatedTask.id ? updatedTask : currentTask,
            ),
          ),
        );
        resetEditingTask();
        addNotification({ message: "Task updated.", type: "success" });
        return true;
      } catch (updateError: any) {
        addNotification({
          message:
            updateError.response?.data?.detail || "Failed to update task title.",
          type: "error",
        });
        return false;
      } finally {
        setSavingTitleTaskId(null);
        pendingTitleSaveRef.current = null;
      }
    })();

    pendingTitleSaveRef.current = savePromise;
    return savePromise;
  };

  const handleBeginEditingTask = async (task: UserTask) => {
    if (editingTaskId !== null && editingTaskId !== task.id) {
      const saved = await commitEditingTask();
      if (!saved) {
        return;
      }
    }

    handleCloseComposer();
    setError(null);
    setEditingTaskId(task.id);
    setEditingTitle(task.title);
  };

  const handleCancelEditingTask = () => {
    pendingTitleSaveRef.current = null;
    setError(null);
    resetEditingTask();
  };

  const handleEditingKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
  ) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void commitEditingTask();
    }

    if (event.key === "Escape") {
      event.preventDefault();
      handleCancelEditingTask();
    }
  };

  const handleOpenComposer = async () => {
    const saved = await commitEditingTask();
    if (!saved) {
      return;
    }

    setError(null);
    setIsComposerOpen(true);
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
    const saved = await commitEditingTask();
    if (!saved) {
      return;
    }

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
    const saved = await commitEditingTask();
    if (!saved) {
      return;
    }

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

  const handleAssignDeadline = async (
    taskId: number,
    dueDate: Date | null,
  ): Promise<boolean> => {
    const saved = await commitEditingTask();
    if (!saved) {
      return false;
    }

    setBusyTaskId(taskId);
    setError(null);

    try {
      const updatedTask = await updateUserTask(taskId, {
        due_at: dueDate ? toLocalDateTimeString(dueDate) : null,
      });

      setTasks((currentTasks) =>
        sortTasks(
          currentTasks.map((currentTask) =>
            currentTask.id === updatedTask.id ? updatedTask : currentTask,
          ),
        ),
      );
      addNotification({
        message: dueDate ? "Deadline updated." : "Deadline cleared.",
        type: "success",
      });
      return true;
    } catch (deadlineError: any) {
      addNotification({
        message:
          deadlineError.response?.data?.detail || "Failed to save deadline.",
        type: "error",
      });
      return false;
    } finally {
      setBusyTaskId(null);
    }
  };

  return (
    <div className="rounded-[2rem] border border-gray-200 bg-white/90 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-gray-700/80 dark:bg-gray-950/62 dark:shadow-black/20">
      <div className="space-y-2">
        <div className="mt-2 flex items-start gap-3">
          <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
            <Check className="h-5 w-5" />
          </div>
          <h2 className="text-2xl font-semibold text-gray-950 dark:text-white">
            To-Do List
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
            {openTasks.length} open
          </span>
          {completedTasks.length > 0 && (
            <span className="inline-flex items-center gap-2 rounded-full border border-gray-300 bg-white/80 px-3 py-1 text-xs font-semibold text-gray-700 dark:border-gray-700 dark:bg-white/5 dark:text-gray-200">
              {completedTasks.length} completed
            </span>
          )}
        </div>
      </div>

      {isComposerOpen ? (
        <form onSubmit={handleCreateTask} className="relative mt-6">
          <input
            ref={titleInputRef}
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="Add a task and press Enter"
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
          onClick={() => void handleOpenComposer()}
          className="mt-6 w-full border-0 border-b border-gray-300 px-1 py-3 text-left text-sm text-gray-700 transition-colors hover:border-orange-400 hover:text-gray-900 dark:border-gray-700 dark:text-gray-200 dark:hover:border-orange-500/40 dark:hover:text-white"
        >
          Add a task...
        </button>
      )}

      {error && (
        <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <div className="mt-6 space-y-6">
        {loading ? (
          <div className="flex items-center gap-3 rounded-[1.5rem] border border-gray-200 bg-white/90 px-4 py-4 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-200">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading your tasks...
          </div>
        ) : (
          <>
            {openTasks.length > 0 && (
              <div className="space-y-3">
                {openTasks.map((task) => {
                  const timeRemainingState = getTimeRemainingState(task, now);
                  const deadline = task.due_at ? parseTaskDeadline(task.due_at) : null;
                  const isBusy =
                    busyTaskId === task.id || savingTitleTaskId === task.id;
                  const isEditing = editingTaskId === task.id;

                  return (
                    <div
                      key={task.id}
                      className="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-4 rounded-[1.75rem] border border-gray-200 bg-gradient-to-br from-white via-white to-orange-50/50 px-4 py-4 shadow-sm shadow-orange-950/5 transition-all hover:-translate-y-0.5 hover:shadow-lg dark:border-gray-700/70 dark:from-gray-900/80 dark:via-gray-900/70 dark:to-orange-500/10"
                    >
                      <button
                        type="button"
                        onClick={() => void handleToggleTask(task)}
                        disabled={isBusy}
                        className="inline-flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-gray-300 bg-white text-gray-400 transition-colors hover:border-orange-400 hover:bg-orange-50 hover:text-orange-600 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-500 dark:hover:border-orange-500/30 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
                        aria-label={`Mark ${task.title} complete`}
                      >
                        {isBusy ? (
                          <Loader2 className="h-4 w-4 animate-spin text-orange-600 dark:text-orange-300" />
                        ) : (
                          <Check className="h-4 w-4" />
                        )}
                      </button>

                      <div className="min-w-0 flex-1">
                        {isEditing ? (
                          <form
                            ref={editingFormRef}
                            onSubmit={(event) => {
                              event.preventDefault();
                              void commitEditingTask();
                            }}
                            className="relative"
                          >
                            <input
                              ref={editingInputRef}
                              type="text"
                              value={editingTitle}
                              onChange={(event) => setEditingTitle(event.target.value)}
                              onKeyDown={handleEditingKeyDown}
                              disabled={isBusy}
                              className="h-10 w-full border-0 border-b border-orange-200 bg-transparent px-0 pr-8 text-sm font-semibold text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 dark:border-orange-500/20 dark:text-white dark:placeholder:text-gray-500"
                            />

                            {isBusy && (
                              <Loader2 className="pointer-events-none absolute right-0 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-orange-600 dark:text-orange-300" />
                            )}
                          </form>
                        ) : (
                          <p
                            onDoubleClick={() => void handleBeginEditingTask(task)}
                            className="cursor-text text-sm font-semibold text-gray-900 dark:text-white"
                            title="Double-click to edit"
                          >
                            {task.title}
                          </p>
                        )}

                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          {timeRemainingState && (
                            <span
                              className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${timeRemainingState.className}`}
                            >
                              {timeRemainingState.label}
                            </span>
                          )}

                          <TaskDeadlinePicker
                            value={deadline}
                            onChange={(date) => handleAssignDeadline(task.id, date)}
                            placeholderText="Add deadline"
                            disabled={isBusy}
                            className="w-auto"
                            inputClassName={DEADLINE_INPUT_CLASS}
                          />
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => void handleDeleteTask(task.id)}
                        disabled={isBusy}
                        className="inline-flex h-12 w-12 shrink-0 self-center items-center justify-center rounded-2xl border border-transparent bg-white/80 text-gray-500 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 dark:bg-white/5 dark:text-gray-400 dark:hover:border-rose-500/10 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
                        aria-label={`Delete ${task.title}`}
                      >
                        <Trash2 className="h-5 w-5" />
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
                    ? "border-t border-gray-200 pt-6 dark:border-gray-700"
                    : ""
                }`}
              >
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-600 dark:text-gray-300">
                  Completed
                </div>

                {completedTasks.map((task) => {
                  const isBusy =
                    busyTaskId === task.id || savingTitleTaskId === task.id;
                  const isEditing = editingTaskId === task.id;
                  const deadline = task.due_at ? parseTaskDeadline(task.due_at) : null;

                  return (
                    <div
                      key={task.id}
                      className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-4 rounded-[1.75rem] border border-gray-200 bg-white/80 px-4 py-4 dark:border-gray-700 dark:bg-gray-900/60"
                    >
                      <button
                        type="button"
                        onClick={() => void handleToggleTask(task)}
                        disabled={isBusy}
                        className="inline-flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-emerald-200 bg-emerald-100 text-emerald-700 transition-colors hover:border-emerald-300 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                        aria-label={`Mark ${task.title} incomplete`}
                      >
                        {isBusy ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Check className="h-4 w-4" />
                        )}
                      </button>

                      <div className="min-w-0 flex-1">
                        {isEditing ? (
                          <form
                            ref={editingFormRef}
                            onSubmit={(event) => {
                              event.preventDefault();
                              void commitEditingTask();
                            }}
                            className="relative"
                          >
                            <input
                              ref={editingInputRef}
                              type="text"
                              value={editingTitle}
                              onChange={(event) => setEditingTitle(event.target.value)}
                              onKeyDown={handleEditingKeyDown}
                              disabled={isBusy}
                              className="h-10 w-full border-0 border-b border-orange-200 bg-transparent px-0 pr-8 text-sm font-semibold text-gray-700 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 dark:border-orange-500/20 dark:text-gray-200 dark:placeholder:text-gray-500"
                            />

                            {isBusy && (
                              <Loader2 className="pointer-events-none absolute right-0 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-orange-600 dark:text-orange-300" />
                            )}
                          </form>
                        ) : (
                          <p
                            onDoubleClick={() => void handleBeginEditingTask(task)}
                            className="cursor-text text-sm font-medium text-gray-600 line-through dark:text-gray-300"
                            title="Double-click to edit"
                          >
                            {task.title}
                          </p>
                        )}

                        {deadline && (
                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            <TaskDeadlinePicker
                              value={deadline}
                              onChange={(date) => handleAssignDeadline(task.id, date)}
                              placeholderText="Add deadline"
                              disabled={isBusy}
                              className="w-auto"
                              inputClassName={DEADLINE_INPUT_CLASS}
                            />
                          </div>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={() => void handleDeleteTask(task.id)}
                        disabled={isBusy}
                        className="inline-flex h-12 w-12 shrink-0 self-center items-center justify-center rounded-2xl border border-transparent bg-white/80 text-gray-500 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 dark:bg-white/5 dark:text-gray-400 dark:hover:border-rose-500/10 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
                        aria-label={`Delete ${task.title}`}
                      >
                        <Trash2 className="h-5 w-5" />
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