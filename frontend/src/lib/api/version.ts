import type { VersionInfo } from "@/types";
import api from "./client";

export const getVersion = async (
  options?: { refresh?: boolean },
): Promise<VersionInfo> => {
  const response = await api.get<VersionInfo>("/version", {
    params: options?.refresh ? { refresh: true } : undefined,
  });
  return response.data;
};
