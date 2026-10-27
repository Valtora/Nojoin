import { clearStoredNotes, storedNotesSignedIn } from "@/lib/notes/noteStore";
import { noteSignedIn } from "@/lib/session";
import { closeTranscriptEventStreams } from "@/lib/transcriptEvents";
import type { User } from "@/types";
import api from "./client";

/**
 * Records who the browser is signed in as. Once the account's id is known,
 * notes kept in this browser for any other account are removed, so they are
 * never shown or sent as this one (`storedNotesSignedIn`).
 */
const signedInAs = (user: { username: string; force_password_change: boolean; id?: number }) => {
  noteSignedIn(user);
  void storedNotesSignedIn(user.id);
};

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
  signedInAs(response.data);
  return response.data;
};

export const logout = async (): Promise<void> => {
  closeTranscriptEventStreams();
  try {
    await api.post("/login/logout");

    } catch (error: unknown) {
    console.error("Logout failed:", error);
  } finally {
    // Notes not saved yet leave the browser with the session.
    await clearStoredNotes();
    if (typeof window !== "undefined") {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload is required: this runs outside React (no router), and the reload clears the signed-in session's in-memory client state
      window.location.href = "/login";
    }
  }
};

export const getCurrentUser = async (): Promise<User> => {
  const response = await api.get<User>("/users/me");
  signedInAs(response.data);
  return response.data;
};
