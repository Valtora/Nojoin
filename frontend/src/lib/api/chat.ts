import { getErrorMessage, isAbortError } from "@/lib/errors";
import type { ChatMessage, RecordingId } from "@/types";
import api, { API_BASE_URL } from "./client";

export const getChatHistory = async (
  recordingId: RecordingId,
): Promise<ChatMessage[]> => {
  const response = await api.get<ChatMessage[]>(
    `/transcripts/${recordingId}/chat`,
  );
  return response.data;
};

export const clearChatHistory = async (recordingId: RecordingId): Promise<void> => {
  await api.delete(`/transcripts/${recordingId}/chat`);
};

export const streamChatMessage = (
  recordingId: RecordingId,
  message: string,
  onToken: (token: string) => void,
  onComplete: () => void,
  onError: (error: string) => void,
  tagIds?: number[],
  onNotesUpdate?: () => void,
): AbortController => {
  const controller = new AbortController();

  fetch(`${API_BASE_URL}/transcripts/${recordingId}/chat`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message, tag_ids: tagIds }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        try {
          const err = await response.json();
          onError(err.detail || "Failed to send message");
        } catch {
          onError(`Error: ${response.statusText}`);
        }
        return;
      }

      if (!response.body) {
        onError("No response body");
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pendingLine = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          if (pendingLine.trim()) {
            handleChatStreamLine(pendingLine, onToken, onError, onNotesUpdate);
          }
          onComplete();
          break;
        }

        const chunk = decoder.decode(value, { stream: true });
        const lines = `${pendingLine}${chunk}`.split("\n");
        pendingLine = lines.pop() ?? "";

        for (const line of lines) {
          handleChatStreamLine(line, onToken, onError, onNotesUpdate);
        }
      }
    })
    .catch((err) => {
      if (isAbortError(err)) {
        return;
      } else {
        onError(getErrorMessage(err, "Network error"));
      }
    });

  return controller;
};

const handleChatStreamLine = (
  line: string,
  onToken: (token: string) => void,
  onError: (error: string) => void,
  onNotesUpdate?: () => void,
) => {
  if (line.startsWith("event: notes_update")) {
    onNotesUpdate?.();
    return;
  }

  if (!line.startsWith("data: ")) {
    return;
  }

  const data = line.slice(6);
  if (data === "[DONE]") {
    return;
  }

  try {
    const parsed = JSON.parse(data);
    if (parsed.token !== undefined) {
      onToken(parsed.token);
    } else if (parsed.error) {
      onError(parsed.error);
    }
  } catch (error) {
    console.error("Failed to parse SSE data", error);
  }
};
