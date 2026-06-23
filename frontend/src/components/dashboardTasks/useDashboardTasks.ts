import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createUserTask,
  deleteUserTask,
  getUserTasks,
  updateUserTask,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { DEFAULT_TIME_ZONE, getUserTimeZone } from "@/lib/timezone";
import { UserTask } from "@/types";

import { parseTaskDeadline, sortTasks } from "./taskUtils";

export function useDashboardTasks() {
  const [tasks, setTasks] = useState<UserTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const [savingTitleTaskId, setSavingTitleTaskId] = useState<number | null>(null);
  const [editingTaskId, setEditingTaskId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [now, setNow] = useState(() => new Date());
  const [timeZone, setTimeZone] = useState(DEFAULT_TIME_ZONE);
  const [deadlineModalTaskId, setDeadlineModalTaskId] = useState<number | null>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const editingFormRef = useRef<HTMLFormElement>(null);
  const editingInputRef = useRef<HTMLInputElement>(null);
  const pendingTitleSaveRef = useRef<Promise<boolean> | null>(null);
  const deadlineTriggerRefs = useRef<Map<number, HTMLButtonElement | null>>(
    new Map(),
  );
  const lastDeadlineTriggerTaskIdRef = useRef<number | null>(null);
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    let cancelled = false;

    const loadTasks = async () => {
      try {
        const data = await getUserTasks();
        if (!cancelled) {
          setTasks(sortTasks(data));
        }

            } catch (loadError: unknown) {
        if (!cancelled) {
          addNotification({
            type: "error",
            message: getErrorMessage(loadError, "Failed to load tasks."),
          });
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
  }, [addNotification]);

  useEffect(() => {
    let cancelled = false;

    void getUserTimeZone().then((resolvedTimeZone) => {
      if (!cancelled) {
        setTimeZone(resolvedTimeZone);
      }
    });

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
    () => sortedTasks.filter((task) => !task.archived_at && !task.completed_at),
    [sortedTasks],
  );
  const completedTasks = useMemo(
    () => sortedTasks.filter((task) => !task.archived_at && Boolean(task.completed_at)),
    [sortedTasks],
  );
  const deadlineModalTask = useMemo(
    () => tasks.find((task) => task.id === deadlineModalTaskId) ?? null,
    [tasks, deadlineModalTaskId],
  );
  const isDeadlineModalSaving =
    deadlineModalTaskId !== null && busyTaskId === deadlineModalTaskId;

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
    if (deadlineModalTaskId !== null && !deadlineModalTask && !isDeadlineModalSaving) {
      setDeadlineModalTaskId(null);
    }
  }, [deadlineModalTaskId, deadlineModalTask, isDeadlineModalSaving]);

  const handleCloseComposer = () => {
    setIsComposerOpen(false);
    setTitle("");
  };

  const handleCloseDeadlineModal = () => {
    setDeadlineModalTaskId(null);

    window.requestAnimationFrame(() => {
      const taskId = lastDeadlineTriggerTaskIdRef.current;
      if (taskId !== null) {
        deadlineTriggerRefs.current.get(taskId)?.focus();
      }
    });
  };

  const resetEditingTask = useCallback(() => {
    setEditingTaskId(null);
    setEditingTitle("");
  }, []);

  const commitEditingTask = useCallback(async (): Promise<boolean> => {
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
      addNotification({
        type: "error",
        message: "Enter a task title before saving it.",
      });
      return false;
    }

    if (trimmedTitle === task.title) {
      resetEditingTask();
      return true;
    }

    setSavingTitleTaskId(task.id);

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

            } catch (updateError: unknown) {
        addNotification({
          message: getErrorMessage(updateError, "Failed to update task title."),
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
  }, [addNotification, editingTaskId, editingTitle, resetEditingTask, tasks]);

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
  }, [commitEditingTask, editingTaskId]);

  const handleBeginEditingTask = async (task: UserTask) => {
    if (editingTaskId !== null && editingTaskId !== task.id) {
      const saved = await commitEditingTask();
      if (!saved) {
        return;
      }
    }

    handleCloseComposer();
    setEditingTaskId(task.id);
    setEditingTitle(task.title);
  };

  const handleCancelEditingTask = () => {
    pendingTitleSaveRef.current = null;
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

    setIsComposerOpen(true);
  };

  const handleOpenDeadlineModal = async (
    task: UserTask,
    trigger: HTMLButtonElement | null,
  ) => {
    const saved = await commitEditingTask();
    if (!saved) {
      return;
    }

    handleCloseComposer();
    lastDeadlineTriggerTaskIdRef.current = task.id;
    deadlineTriggerRefs.current.set(task.id, trigger);
    setDeadlineModalTaskId(task.id);
  };

  const handleCreateTask = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      addNotification({
        type: "error",
        message: "Enter a task title before adding it.",
      });
      return;
    }

    setSubmitting(true);

    try {
      const createdTask = await createUserTask({
        title: trimmedTitle,
      });

      setTasks((currentTasks) => sortTasks([...currentTasks, createdTask]));
      setTitle("");
      setIsComposerOpen(false);
      addNotification({ message: "Task added.", type: "success" });

        } catch (createError: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(createError, "Failed to add task."),
      });
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

        } catch (toggleError: unknown) {
      addNotification({
        message: getErrorMessage(toggleError, "Failed to update task state."),
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

    try {
      await deleteUserTask(taskId);
      setTasks((currentTasks) =>
        currentTasks.filter((currentTask) => currentTask.id !== taskId),
      );
      addNotification({ message: "Task removed.", type: "success" });

        } catch (deleteError: unknown) {
      addNotification({
        message: getErrorMessage(deleteError, "Failed to delete task."),
        type: "error",
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleArchiveTask = async (task: UserTask) => {
    const saved = await commitEditingTask();
    if (!saved) {
      return;
    }

    setBusyTaskId(task.id);

    try {
      await updateUserTask(task.id, {
        archived: true,
      });
      setTasks((currentTasks) =>
        currentTasks.filter((currentTask) => currentTask.id !== task.id),
      );
      addNotification({ message: "Task archived.", type: "success" });

        } catch (archiveError: unknown) {
      addNotification({
        message: getErrorMessage(archiveError, "Failed to archive task."),
        type: "error",
      });
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleSaveDeadline = async (dueDate: Date | null): Promise<boolean> => {
    if (deadlineModalTaskId === null) {
      return false;
    }

    setBusyTaskId(deadlineModalTaskId);

    try {
      const updatedTask = await updateUserTask(deadlineModalTaskId, {
        due_at: dueDate ? dueDate.toISOString() : null,
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

        } catch (deadlineError: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(deadlineError, "Failed to save deadline."),
      });
      return false;
    } finally {
      setBusyTaskId(null);
    }
  };

  return {
    now,
    timeZone,
    loading,
    submitting,
    busyTaskId,
    savingTitleTaskId,
    editingTaskId,
    editingTitle,
    setEditingTitle,
    isComposerOpen,
    title,
    setTitle,
    openTasks,
    completedTasks,
    deadlineModalTask,
    isDeadlineModalSaving,
    parseTaskDeadlineForModal: (value: string) => parseTaskDeadline(value),
    titleInputRef,
    editingFormRef,
    editingInputRef,
    deadlineTriggerRefs,
    commitEditingTask,
    handleEditingKeyDown,
    handleComposerKeyDown,
    handleOpenComposer,
    handleCreateTask,
    handleBeginEditingTask,
    handleToggleTask,
    handleArchiveTask,
    handleDeleteTask,
    handleOpenDeadlineModal,
    handleCloseDeadlineModal,
    handleSaveDeadline,
  };
}

export type UseDashboardTasksReturn = ReturnType<typeof useDashboardTasks>;
