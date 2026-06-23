import type { PeopleTag, RecordingId, Tag } from "@/types";
import api from "./client";

export const getTags = async (): Promise<Tag[]> => {
  const response = await api.get<Tag[]>("/tags/");
  return response.data;
};

export const createTag = async (
  name: string,
  color?: string,
  parent_id?: number,
): Promise<Tag> => {
  const response = await api.post<Tag>("/tags/", { name, color, parent_id });
  return response.data;
};

export const updateTag = async (
  tagId: number,
  data: { name?: string; color?: string; parent_id?: number | null },
): Promise<Tag> => {
  const response = await api.patch<Tag>(`/tags/${tagId}`, data);
  return response.data;
};

export const addTagToRecording = async (
  recordingId: RecordingId,
  tagName: string,
): Promise<void> => {
  await api.post(`/tags/recordings/${recordingId}`, { name: tagName });
};

export const removeTagFromRecording = async (
  recordingId: RecordingId,
  tagName: string,
): Promise<void> => {
  await api.delete(`/tags/recordings/${recordingId}/${tagName}`);
};

export const deleteTag = async (id: number): Promise<void> => {
  await api.delete(`/tags/${id}`);
};

export const getPeopleTags = async (): Promise<PeopleTag[]> => {
  const response = await api.get<PeopleTag[]>("/people-tags/");
  return response.data;
};

export const createPeopleTag = async (
  name: string,
  color?: string,
  parent_id?: number,
): Promise<PeopleTag> => {
  const response = await api.post<PeopleTag>("/people-tags/", {
    name,
    color,
    parent_id,
  });
  return response.data;
};

export const updatePeopleTag = async (
  id: number,
  data: { name?: string; color?: string; parent_id?: number },
): Promise<PeopleTag> => {
  const response = await api.patch<PeopleTag>(`/people-tags/${id}`, data);
  return response.data;
};

export const deletePeopleTag = async (id: number): Promise<void> => {
  await api.delete(`/people-tags/${id}`);
};

export const batchAddTagToRecordings = async (
  ids: RecordingId[],
  tagName: string,
): Promise<void> => {
  await api.post("/tags/batch/add", { recording_ids: ids, tag_name: tagName });
};

export const batchRemoveTagFromRecordings = async (
  ids: RecordingId[],
  tagName: string,
): Promise<void> => {
  await api.post("/tags/batch/remove", {
    recording_ids: ids,
    tag_name: tagName,
  });
};
