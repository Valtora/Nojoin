import { useEffect, useState } from "react";

import { getUserTasks, updateUserTask } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId, UserTask } from "@/types";

/** Linked tasks share the Tasks workspace's persistence and completion path. */
export function useRecordingTasks(recordingId: RecordingId | undefined) {
  const [loaded, setLoaded] = useState<{ recordingId: RecordingId; tasks: UserTask[] } | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<number | null>(null);
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    if (!recordingId) return;
    let cancelled = false;
    void getUserTasks("all", recordingId).then((tasks) => {
      if (!cancelled) setLoaded({ recordingId, tasks });
    }).catch((error: unknown) => {
      if (!cancelled) {
        setLoaded({ recordingId, tasks: [] });
        addNotification({ type: "error", message: getErrorMessage(error, "Failed to load linked tasks.") });
      }
    });
    return () => { cancelled = true; };
  }, [recordingId, addNotification]);

  const toggleTask = async (task: UserTask) => {
    if (busyTaskId !== null) return;
    setBusyTaskId(task.id);
    try {
      const updated = await updateUserTask(task.id, { completed: !task.completed_at });
      setLoaded((current) => {
        if (!current || current.recordingId !== recordingId) return current;
        return { ...current, tasks: current.tasks.map((item) => item.id === updated.id ? updated : item) };
      });
    } catch (error: unknown) {
      addNotification({ type: "error", message: getErrorMessage(error, "Failed to update task state.") });
    } finally {
      setBusyTaskId(null);
    }
  };

  const loading = Boolean(recordingId) && loaded?.recordingId !== recordingId;
  return { tasks: loaded?.recordingId === recordingId ? loaded?.tasks ?? [] : [], loading, busyTaskId, toggleTask };
}
