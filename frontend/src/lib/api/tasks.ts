import type { RecordingId, UserTask, UserTaskListParams } from "@/types";
import api from "./client";

export type UserTaskStatusFilter = NonNullable<UserTaskListParams["status"]>;

export interface UserTaskListOptions {
  /** Only tasks linked to this meeting. */
  recordingId?: RecordingId;
  /** Only tasks with any of these tags. */
  tagIds?: number[];
}

export const getUserTasks = async (
  status: UserTaskStatusFilter = "active",
  { recordingId, tagIds = [] }: UserTaskListOptions = {},
): Promise<UserTask[]> => {
  // URLSearchParams repeats tag_ids=1&tag_ids=2, the form FastAPI reads as a list.
  const params = new URLSearchParams({ status });
  if (recordingId !== undefined) params.set("recording_id", recordingId);
  tagIds.forEach((id) => params.append("tag_ids", id.toString()));
  const response = await api.get<UserTask[]>("/tasks/", {
    params,
  });
  return response.data;
};

export const createUserTask = async (data: {
  title: string;
  body?: string | null;
  due_at?: string | null;
  tag_ids?: number[];
  recording_ids?: string[];
}): Promise<UserTask> => {
  const response = await api.post<UserTask>("/tasks/", data);
  return response.data;
};

export const updateUserTask = async (
  taskId: number,
  data: {
    title?: string;
    body?: string | null;
    due_at?: string | null;
    completed?: boolean;
    archived?: boolean;
    tag_ids?: number[];
    recording_ids?: string[];
  },
): Promise<UserTask> => {
  const response = await api.patch<UserTask>(`/tasks/${taskId}`, data);
  return response.data;
};

export const deleteUserTask = async (taskId: number): Promise<void> => {
  await api.delete(`/tasks/${taskId}`);
};
