import type { UpdateNotice, UpdateNoticeAction, VersionInfo } from "@/types";
import api from "./client";

export const getVersion = async (
  options?: { refresh?: boolean },
): Promise<VersionInfo> => {
  const response = await api.get<VersionInfo>("/version", {
    params: options?.refresh ? { refresh: true } : undefined,
  });
  return response.data;
};

export const getUpdateNotice = async (): Promise<UpdateNotice> => {
  const response = await api.get<UpdateNotice>("/version/update-notice");
  return response.data;
};

export const dismissUpdateNotice = async (
  action: UpdateNoticeAction,
  version: string,
): Promise<void> => {
  await api.post("/version/update-notice", { action, version });
};
