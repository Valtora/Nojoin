import type { RecordingId, UserTask, UserTaskListParams } from "@/types";
import api from "./client";

export type UserTaskStatusFilter = NonNullable<UserTaskListParams["status"]>;

export const getUserTasks = async (
  status: UserTaskStatusFilter = "active",
  recordingId?: RecordingId,
): Promise<UserTask[]> => {
  const params: UserTaskListParams = { status };
  if (recordingId !== undefined) params.recording_id = recordingId;
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
