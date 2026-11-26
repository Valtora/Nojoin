import type { RecordingId } from "@/types";
import api from "./client";

export type ShareLevel = "view" | "edit";

export interface ShareUser {
  id: number;
  username: string;
  display_name?: string | null;
}

/** One grant on a meeting: to a user, or to everyone in a provider group. */
export interface RecordingShare {
  id: number;
  user: ShareUser | null;
  group: string | null;
  level: ShareLevel;
}

export interface DirectoryGroup {
  name: string;
  member_count: number;
}

export type ShareGrantee = { user_id: number } | { group: string };

export const listShares = async (recordingId: RecordingId): Promise<RecordingShare[]> => {
  const response = await api.get<RecordingShare[]>(`/recordings/${recordingId}/shares`);
  return response.data;
};

/** Shares the meeting, or changes the level of an existing share. */
export const upsertShare = async (
  recordingId: RecordingId,
  grantee: ShareGrantee,
  level: ShareLevel,
): Promise<RecordingShare[]> => {
  const response = await api.put<RecordingShare[]>(`/recordings/${recordingId}/shares`, {
    ...grantee,
    level,
  });
  return response.data;
};

export const deleteShare = async (
  recordingId: RecordingId,
  shareId: number,
): Promise<RecordingShare[]> => {
  const response = await api.delete<RecordingShare[]>(
    `/recordings/${recordingId}/shares/${shareId}`,
  );
  return response.data;
};

/** Stops seeing a meeting someone shared with you directly. */
export const leaveShare = async (recordingId: RecordingId): Promise<void> => {
  await api.delete(`/recordings/${recordingId}/shares/me`);
};

export const getUserDirectory = async (): Promise<ShareUser[]> => {
  const response = await api.get<ShareUser[]>("/users/directory");
  return response.data;
};

/** The provider groups the signed-in user belongs to, with member counts. */
export const getGroupDirectory = async (): Promise<DirectoryGroup[]> => {
  const response = await api.get<DirectoryGroup[]>("/users/directory/groups");
  return response.data;
};
