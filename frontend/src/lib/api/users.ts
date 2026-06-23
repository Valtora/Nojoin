import type { Invitation, User } from "@/types";
import api from "./client";

export const getUsers = async (
  skip = 0,
  limit = 100,
  search = "",
): Promise<{ items: User[]; total: number }> => {
  const params = new URLSearchParams({
    skip: skip.toString(),
    limit: limit.toString(),
  });
  if (search) params.append("search", search);

  const response = await api.get<{ items: User[]; total: number }>(
    `/users?${params.toString()}`,
  );
  return response.data;
};

export const getUserMe = async (): Promise<User> => {
  const response = await api.get<User>("/users/me");
  return response.data;
};

export const updateUserMe = async (data: {
  username?: string;
}): Promise<User> => {
  const response = await api.put<User>("/users/me", data);
  return response.data;
};

export const updatePasswordMe = async (data: {
  current_password: string;
  new_password: string;
}): Promise<void> => {
  await api.put("/users/me/password", data);
};

export const createUser = async (data: {
  username: string;
  password: string;
  role: string;
}): Promise<User> => {
  const response = await api.post<User>("/users/", data);
  return response.data;
};

export const updateUser = async (
  userId: number,
  data: {
    username?: string;
    password?: string;
    role?: string;
    is_active?: boolean;
  },
): Promise<User> => {
  const response = await api.patch<User>(`/users/${userId}`, data);
  return response.data;
};

export const updateUserRole = async (
  userId: number,
  role: string,
): Promise<User> => {
  const response = await api.patch<User>(`/users/${userId}/role`, { role });
  return response.data;
};

export const deleteUser = async (userId: number): Promise<User> => {
  const response = await api.delete<User>(`/users/${userId}`);
  return response.data;
};

export const getInvitations = async (): Promise<Invitation[]> => {
  const response = await api.get<Invitation[]>("/invitations/");
  return response.data;
};

export const createInvitation = async (
  role: string,
  expires_in_days: number,
  max_uses: number,
): Promise<Invitation> => {
  const response = await api.post<Invitation>("/invitations/", {
    role,
    expires_in_days,
    max_uses,
  });
  return response.data;
};

export const revokeInvitation = async (id: number): Promise<Invitation> => {
  const response = await api.post<Invitation>(`/invitations/${id}/revoke`);
  return response.data;
};

export const deleteInvitation = async (id: number): Promise<Invitation> => {
  const response = await api.delete<Invitation>(`/invitations/${id}`);
  return response.data;
};

export const validateInvitation = async (
  code: string,
): Promise<{ valid: boolean; role: string; inviter?: string }> => {
  const response = await api.get<{
    valid: boolean;
    role: string;
    inviter?: string;
  }>(`/invitations/validate/${code}`);
  return response.data;
};

export const registerUser = async (
  username: string,
  password: string,
  invite_code: string,
): Promise<User> => {
  const payload = {
    username,
    password,
    invite_code,
  };
  const response = await api.post<User>("/users/register", payload);
  return response.data;
};
