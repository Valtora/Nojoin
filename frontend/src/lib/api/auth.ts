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
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload is required: this runs outside React (no router), and the reload clears the signed-in session's in-memory client state
      window.location.href = "/login";
    }
  }
};

export const getCurrentUser = async (): Promise<User> => {
  const response = await api.get<User>("/users/me");
  return response.data;
};
