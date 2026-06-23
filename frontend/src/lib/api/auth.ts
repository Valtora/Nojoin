import type { User } from "@/types";
import api from "./client";

export const login = async (
  username: string,
  password: string,
): Promise<{
  force_password_change: boolean;
  is_superuser: boolean;
  username: string;
}> => {
  const formData = new FormData();
  formData.append("username", username);
  formData.append("password", password);

  const response = await api.post<{
    force_password_change: boolean;
    is_superuser: boolean;
    username: string;
  }>("/login/session", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
    },
  });
  return response.data;
};

export const logout = async (): Promise<void> => {
  try {
    await api.post("/login/logout");

    } catch (error: unknown) {
    console.error("Logout failed:", error);
  } finally {
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  }
};

export const getCurrentUser = async (): Promise<User> => {
  const response = await api.get<User>("/users/me");
  return response.data;
};
