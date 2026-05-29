"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  Calendar,
  Check,
  Clock,
  FileAudio,
  Loader2,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";

import AmbientWorkspace from "./AmbientWorkspace";
import {
  createUserTask,
  deleteUserTask,
  getRecordings,
  getTags,
  getUserTasks,
  updateUserTask,
} from "@/lib/api";
import { getColorByKey } from "@/lib/constants";
import { useNotificationStore } from "@/lib/notificationStore";
import type { Recording, RecordingId, Tag, UserTask } from "@/types";
import TaskDeadlineModal from "./ui/TaskDeadlineModal";

type TaskView = "open" | "completed" | "archived";

interface TaskDraft {
  title: string;
  body: string;
  dueAt: string;
  tagIds: number[];
  recordingIds: RecordingId[];
}

const EMPTY_DRAFT: TaskDraft = {
  title: "",
  body: "",
  dueAt: "",
  tagIds: [],
  recordingIds: [],
};

function getErrorMessage(error: unknown, fallback: string): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof error.response === "object" &&
    error.response !== null &&
    "data" in error.response &&
    typeof error.response.data === "object" &&
    error.response.data !== null &&
    "detail" in error.response.data &&
    typeof error.response.data.detail === "string"
  ) {
    return error.response.data.detail;
  }

  return fallback;
}

function formatTaskDate(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function getTaskTagIds(task: UserTask): number[] {
  return task.tags?.map((tag) => tag.id) ?? [];
}

function getTaskRecordingIds(task: UserTask): RecordingId[] {
  return task.linked_recordings?.map((recording) => recording.id) ?? [];
}

function sortTasks(tasks: UserTask[]): UserTask[] {
  return [...tasks].sort((left, right) => {
    const leftArchive = left.archived_at ? new Date(left.archived_at).getTime() : 0;
    const rightArchive = right.archived_at ? new Date(right.archived_at).getTime() : 0;
    if (leftArchive || rightArchive) {
      return rightArchive - leftArchive;
    }

    const leftDue = left.due_at ? new Date(left.due_at).getTime() : Number.MAX_SAFE_INTEGER;
    const rightDue = right.due_at ? new Date(right.due_at).getTime() : Number.MAX_SAFE_INTEGER;
    if (leftDue !== rightDue) {
      return leftDue - rightDue;
    }

    return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
  });
}

function TagSelector({
  tags,
  selectedIds,
  onChange,
}: {
  tags: Tag[];
  selectedIds: number[];
  onChange: (ids: number[]) => void;
}) {
  const selected = new Set(selectedIds);

  if (tags.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-gray-300 px-4 py-3 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
        No tags yet. Create tags from the sidebar to reuse them on tasks and recordings.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {tags.map((tag) => {
        const color = getColorByKey(tag.color);
        const isSelected = selected.has(tag.id);

        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => {
              onChange(
                isSelected
                  ? selectedIds.filter((id) => id !== tag.id)
                  : [...selectedIds, tag.id],
              );
            }}
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors ${
              isSelected
                ? `${color.bg} ${color.border} ${color.text}`
                : "border-gray-200 bg-white text-gray-600 hover:border-orange-200 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${color.dot}`} />
            {tag.name}
          </button>
        );
      })}
    </div>
  );
}

function RecordingSelector({
  recordings,
  selectedIds,
  onChange,
}: {
  recordings: Recording[];
  selectedIds: RecordingId[];
  onChange: (ids: RecordingId[]) => void;
}) {
  const selected = new Set(selectedIds);

  if (recordings.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-gray-300 px-4 py-3 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
        No recordings available to link yet.
      </p>
    );
  }

  return (
    <div className="max-h-32 space-y-2 overflow-y-auto rounded-2xl border border-gray-200 bg-white/75 p-2 dark:border-gray-700 dark:bg-gray-950/60">
      {recordings.map((recording) => {
        const isSelected = selected.has(recording.id);

        return (
          <button
            key={recording.id}
            type="button"
            onClick={() => {
              onChange(
                isSelected
                  ? selectedIds.filter((id) => id !== recording.id)
                  : [...selectedIds, recording.id],
              );
            }}
            className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-semibold transition-colors ${
              isSelected
                ? "bg-orange-100 text-orange-800 dark:bg-orange-500/15 dark:text-orange-200"
                : "text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-900"
            }`}
          >
            <FileAudio className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{recording.name}</span>
          </button>
        );
      })}
    </div>
  );
}

function DeadlinePickerButton({
  value,
  onClick,
}: {
  value: string;
  onClick: () => void;
}) {
  const formatted = formatTaskDate(value);

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={onClick}
        className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-orange-200 bg-white text-orange-600 outline-none transition-colors hover:border-orange-400 hover:bg-orange-50 hover:text-orange-700 focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-gray-700 dark:bg-gray-950 dark:text-orange-300 dark:hover:border-orange-500/40 dark:hover:bg-orange-500/10 dark:hover:text-orange-200"
        aria-label={formatted ? `Edit deadline, ${formatted}` : "Set deadline"}
        title={formatted ? `Edit deadline: ${formatted}` : "Set deadline"}
      >
        <Calendar className="block h-4 w-4 shrink-0" />
      </button>
      {formatted && (
        <span className="min-w-0 truncate text-sm font-semibold text-gray-700 dark:text-gray-200">
          {formatted}
        </span>
      )}
    </div>
  );
}

export default function TasksWorkspace() {
  const [tasks, setTasks] = useState<UserTask[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [view, setView] = useState<TaskView>("open");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [draft, setDraft] = useState<TaskDraft>(EMPTY_DRAFT);
  const [editingTaskId, setEditingTaskId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<TaskDraft>(EMPTY_DRAFT);
  const [deadlineTarget, setDeadlineTarget] = useState<"create" | "edit" | null>(
    null,
  );
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [taskData, tagData, recordingData] = await Promise.all([
          getUserTasks("all"),
          getTags(),
          getRecordings({ include_archived: true }),
        ]);
        if (!cancelled) {
          setTasks(sortTasks(taskData));
          setTags(tagData);
          setRecordings(recordingData.filter((recording) => !recording.is_deleted));
        }
      } catch (error: unknown) {
        if (!cancelled) {
          addNotification({
            type: "error",
            message: getErrorMessage(error, "Failed to load tasks."),
          });
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();

    return () => {
      cancelled = true;
    };
  }, [addNotification]);

  useEffect(() => {
    const handleTagsUpdated = () => {
      getTags()
        .then(setTags)
        .catch((error: unknown) => {
          addNotification({
            type: "error",
            message: getErrorMessage(error, "Failed to refresh tags."),
          });
        });
    };

    window.addEventListener("tags-updated", handleTagsUpdated);
    return () => window.removeEventListener("tags-updated", handleTagsUpdated);
  }, [addNotification]);

  const taskGroups = useMemo(() => {
    const open = tasks.filter((task) => !task.archived_at && !task.completed_at);
    const completed = tasks.filter((task) => !task.archived_at && task.completed_at);
    const archived = tasks.filter((task) => task.archived_at);
    return { open, completed, archived };
  }, [tasks]);

  const visibleTasks = taskGroups[view];

  const upsertTask = (updatedTask: UserTask) => {
    setTasks((currentTasks) =>
      sortTasks(
        currentTasks.map((task) =>
          task.id === updatedTask.id ? updatedTask : task,
        ),
      ),
    );
  };

  const handleCreateTask = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmedTitle = draft.title.trim();
    if (!trimmedTitle) {
      addNotification({ type: "error", message: "Enter a task title." });
      return;
    }

    setSubmitting(true);
    try {
      const createdTask = await createUserTask({
        title: trimmedTitle,
        body: draft.body.trim() || null,
        due_at: draft.dueAt ? new Date(draft.dueAt).toISOString() : null,
        tag_ids: draft.tagIds,
        recording_ids: draft.recordingIds,
      });
      setTasks((currentTasks) => sortTasks([...currentTasks, createdTask]));
      setDraft(EMPTY_DRAFT);
      setIsCreateOpen(false);
      addNotification({ type: "success", message: "Task created." });
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Failed to create task."),
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleTaskUpdate = async (
    task: UserTask,
    payload: Parameters<typeof updateUserTask>[1],
    successMessage: string,
  ) => {
    setBusyTaskId(task.id);
    try {
      const updatedTask = await updateUserTask(task.id, payload);
      upsertTask(updatedTask);
      addNotification({ type: "success", message: successMessage });
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Failed to update task."),
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleDeleteTask = async (task: UserTask) => {
    setBusyTaskId(task.id);
    try {
      await deleteUserTask(task.id);
      setTasks((currentTasks) =>
        currentTasks.filter((currentTask) => currentTask.id !== task.id),
      );
      addNotification({ type: "success", message: "Task deleted." });
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Failed to delete task."),
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  const beginEdit = (task: UserTask) => {
    setEditingTaskId(task.id);
    setEditDraft({
      title: task.title,
      body: task.body ?? "",
      dueAt: task.due_at ?? "",
      tagIds: getTaskTagIds(task),
      recordingIds: getTaskRecordingIds(task),
    });
  };

  const cancelEdit = () => {
    setEditingTaskId(null);
    setEditDraft(EMPTY_DRAFT);
  };

  const saveEdit = async (task: UserTask) => {
    const title = editDraft.title.trim();
    if (!title) {
      addNotification({ type: "error", message: "Enter a task title." });
      return;
    }

    await handleTaskUpdate(
      task,
      {
        title,
        body: editDraft.body.trim() || null,
        due_at: editDraft.dueAt ? new Date(editDraft.dueAt).toISOString() : null,
        tag_ids: editDraft.tagIds,
        recording_ids: editDraft.recordingIds,
      },
      "Task updated.",
    );
    cancelEdit();
  };

  const deadlineModalValue =
    deadlineTarget === "create"
      ? draft.dueAt
        ? new Date(draft.dueAt)
        : null
      : deadlineTarget === "edit"
        ? editDraft.dueAt
          ? new Date(editDraft.dueAt)
          : null
        : null;

  const deadlineModalTitle =
    deadlineTarget === "create"
      ? draft.title || "New task"
      : editDraft.title || "Task";

  const handleSaveDeadline = (date: Date | null): boolean => {
    const nextValue = date ? date.toISOString() : "";
    if (deadlineTarget === "create") {
      setDraft((current) => ({ ...current, dueAt: nextValue }));
      return true;
    }

    if (deadlineTarget === "edit") {
      setEditDraft((current) => ({ ...current, dueAt: nextValue }));
      return true;
    }

    return false;
  };

  return (
    <AmbientWorkspace
      contentClassName="max-w-7xl gap-6"
      paddingClassName="py-6 md:py-8"
    >
      <section className="rounded-[2rem] border border-white/60 bg-white/85 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/65 dark:shadow-black/20 md:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-4xl font-semibold tracking-tight text-gray-950 dark:text-white">
              Manage Tasks
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-gray-600 dark:text-gray-300">
              Capture follow-ups, add shared recording tags, archive stale work, and restore archived tasks when they become relevant again.
            </p>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="grid grid-cols-3 gap-2 rounded-2xl border border-gray-200 bg-gray-50 p-1 dark:border-gray-700 dark:bg-gray-900">
              {(["open", "completed", "archived"] as TaskView[]).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setView(item)}
                  className={`rounded-xl px-4 py-2 text-sm font-semibold capitalize transition-colors ${
                    view === item
                      ? "bg-white text-orange-700 shadow-sm dark:bg-gray-800 dark:text-orange-300"
                      : "text-gray-600 hover:text-gray-950 dark:text-gray-300 dark:hover:text-white"
                  }`}
                >
                  {item}
                </button>
              ))}
            </div>
            {!isCreateOpen && (
              <button
                type="button"
                onClick={() => setIsCreateOpen(true)}
                className="inline-flex h-[3.625rem] items-center justify-center gap-2 whitespace-nowrap rounded-2xl bg-orange-600 px-5 text-sm font-semibold text-white transition-colors hover:bg-orange-700"
              >
                <Plus className="h-4 w-4" />
                Create Task
              </button>
            )}
          </div>
        </div>

        {isCreateOpen && (
          <form
            onSubmit={handleCreateTask}
            className="mt-8 grid items-stretch gap-4 rounded-[1.75rem] border border-orange-100 bg-orange-50/50 p-4 dark:border-orange-500/15 dark:bg-orange-500/5 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.42fr)]"
          >
            <div className="flex flex-col gap-3">
              <input
                value={draft.title}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, title: event.target.value }))
                }
                placeholder="Task title"
                className="h-12 w-full rounded-2xl border border-orange-200 bg-white px-4 text-sm font-semibold text-gray-950 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-gray-700 dark:bg-gray-950 dark:text-white"
              />
              <textarea
                value={draft.body}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, body: event.target.value }))
                }
                placeholder="Add context, notes, or acceptance criteria"
                className="min-h-48 flex-1 resize-none rounded-2xl border border-orange-200 bg-white px-4 py-3 text-sm text-gray-800 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
            </div>

            <div className="flex flex-col gap-4">
              <DeadlinePickerButton
                value={draft.dueAt}
                onClick={() => setDeadlineTarget("create")}
              />
              <TagSelector
                tags={tags}
                selectedIds={draft.tagIds}
                onChange={(tagIds) =>
                  setDraft((current) => ({ ...current, tagIds }))
                }
              />
              <RecordingSelector
                recordings={recordings}
                selectedIds={draft.recordingIds}
                onChange={(recordingIds) =>
                  setDraft((current) => ({ ...current, recordingIds }))
                }
              />
              <div className="mt-auto flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setDraft(EMPTY_DRAFT);
                    setIsCreateOpen(false);
                  }}
                  disabled={submitting}
                  className="inline-flex items-center justify-center rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200"
                  aria-label="Cancel task creation"
                >
                  <X className="h-4 w-4" />
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {submitting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  Add task
                </button>
              </div>
            </div>
          </form>
        )}
      </section>

      <section className="grid gap-4">
        {loading ? (
          <div className="rounded-[1.75rem] border border-gray-200 bg-white/85 p-6 text-sm text-gray-600 shadow-lg shadow-orange-950/5 dark:border-gray-700 dark:bg-gray-950/65 dark:text-gray-300">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading tasks...
          </div>
        ) : visibleTasks.length === 0 ? (
          <div className="rounded-[1.75rem] border border-dashed border-gray-300 bg-white/70 p-8 text-center text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-950/50 dark:text-gray-300">
            No {view} tasks.
          </div>
        ) : (
          visibleTasks.map((task) => {
            const isEditing = editingTaskId === task.id;
            const isBusy = busyTaskId === task.id;
            const dueLabel = formatTaskDate(task.due_at);
            const archivedLabel = formatTaskDate(task.archived_at);

            return (
              <article
                key={task.id}
                className="rounded-[1.75rem] border border-gray-200 bg-white/85 p-5 shadow-lg shadow-orange-950/5 dark:border-gray-700 dark:bg-gray-950/65 dark:shadow-black/20"
              >
                {isEditing ? (
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(17rem,0.45fr)]">
                    <div className="space-y-3">
                      <input
                        value={editDraft.title}
                        onChange={(event) =>
                          setEditDraft((current) => ({
                            ...current,
                            title: event.target.value,
                          }))
                        }
                        className="h-11 w-full rounded-2xl border border-orange-200 bg-white px-4 text-sm font-semibold text-gray-950 outline-none focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
                      />
                      <textarea
                        value={editDraft.body}
                        onChange={(event) =>
                          setEditDraft((current) => ({
                            ...current,
                            body: event.target.value,
                          }))
                        }
                        rows={4}
                        className="w-full resize-none rounded-2xl border border-orange-200 bg-white px-4 py-3 text-sm text-gray-800 outline-none focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
                      />
                    </div>
                    <div className="space-y-4">
                      <DeadlinePickerButton
                        value={editDraft.dueAt}
                        onClick={() => setDeadlineTarget("edit")}
                      />
                      <TagSelector
                        tags={tags}
                        selectedIds={editDraft.tagIds}
                        onChange={(tagIds) =>
                          setEditDraft((current) => ({ ...current, tagIds }))
                        }
                      />
                      <RecordingSelector
                        recordings={recordings}
                        selectedIds={editDraft.recordingIds}
                        onChange={(recordingIds) =>
                          setEditDraft((current) => ({
                            ...current,
                            recordingIds,
                          }))
                        }
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => void saveEdit(task)}
                          disabled={isBusy}
                          className="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-orange-700 disabled:opacity-60"
                        >
                          {isBusy && <Loader2 className="h-4 w-4 animate-spin" />}
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={cancelEdit}
                          className="inline-flex items-center justify-center rounded-2xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 hover:border-gray-300 dark:border-gray-700 dark:text-gray-200"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-semibold text-gray-950 dark:text-white">
                          {task.title}
                        </h2>
                        {task.completed_at && !task.archived_at && (
                          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                            Completed
                          </span>
                        )}
                        {task.archived_at && (
                          <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
                            Archived
                          </span>
                        )}
                      </div>

                      {task.body && (
                        <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-600 dark:text-gray-300">
                          {task.body}
                        </p>
                      )}

                      <div className="mt-4 flex flex-wrap items-center gap-2">
                        {dueLabel && (
                          <span className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-semibold text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                            <Clock className="h-3.5 w-3.5" />
                            {dueLabel}
                          </span>
                        )}
                        {archivedLabel && (
                          <span className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
                            <Archive className="h-3.5 w-3.5" />
                            {archivedLabel}
                          </span>
                        )}
                        {task.tags?.map((tag) => {
                          const color = getColorByKey(tag.color);
                          return (
                            <span
                              key={tag.id}
                              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${color.bg} ${color.border} ${color.text}`}
                            >
                              <span className={`h-2 w-2 rounded-full ${color.dot}`} />
                              {tag.name}
                            </span>
                          );
                        })}
                        {task.linked_recordings?.map((recording) => (
                          <Link
                            key={recording.id}
                            href={`/recordings/${recording.id}`}
                            className="inline-flex items-center gap-2 rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700 transition-colors hover:border-sky-300 hover:text-sky-900 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-300 dark:hover:border-sky-400/40 dark:hover:text-sky-200"
                          >
                            <FileAudio className="h-3.5 w-3.5" />
                            {recording.name}
                          </Link>
                        ))}
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => beginEdit(task)}
                        disabled={isBusy}
                        className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:border-orange-200 hover:text-orange-700 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
                      >
                        <Pencil className="h-4 w-4" />
                        Edit
                      </button>
                      {!task.archived_at && (
                        <button
                          type="button"
                          onClick={() =>
                            void handleTaskUpdate(
                              task,
                              { completed: !task.completed_at },
                              task.completed_at
                                ? "Task reopened."
                                : "Task completed.",
                            )
                          }
                          disabled={isBusy}
                          className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:border-emerald-200 hover:text-emerald-700 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-emerald-500/30 dark:hover:text-emerald-300"
                        >
                          {task.completed_at ? (
                            <RotateCcw className="h-4 w-4" />
                          ) : (
                            <Check className="h-4 w-4" />
                          )}
                          {task.completed_at ? "Reopen" : "Complete"}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          void handleTaskUpdate(
                            task,
                            { archived: !task.archived_at },
                            task.archived_at
                              ? "Task restored."
                              : "Task archived.",
                          )
                        }
                        disabled={isBusy}
                        className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:border-amber-200 hover:text-amber-700 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-amber-500/30 dark:hover:text-amber-300"
                      >
                        {task.archived_at ? (
                          <ArchiveRestore className="h-4 w-4" />
                        ) : (
                          <Archive className="h-4 w-4" />
                        )}
                        {task.archived_at ? "Restore" : "Archive"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteTask(task)}
                        disabled={isBusy}
                        className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:border-rose-200 hover:text-rose-700 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-rose-500/30 dark:hover:text-rose-300"
                      >
                        {isBusy ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                        Delete
                      </button>
                    </div>
                  </div>
                )}
              </article>
            );
          })
        )}
      </section>
      <TaskDeadlineModal
        isOpen={deadlineTarget !== null}
        taskTitle={deadlineModalTitle}
        value={deadlineModalValue}
        onClose={() => setDeadlineTarget(null)}
        onSave={handleSaveDeadline}
      />
    </AmbientWorkspace>
  );
}
