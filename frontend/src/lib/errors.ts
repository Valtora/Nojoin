import axios from "axios";

export interface ApiErrorPayload extends Record<string, unknown> {
  detail?: unknown;
  message?: unknown;
}

export interface AxiosErrorLike {
  response?: {
    data?: unknown;
    status?: number;
  };
  message?: unknown;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isApiErrorPayload(value: unknown): value is ApiErrorPayload {
  return isRecord(value);
}

export function isAxiosErrorLike(error: unknown): error is AxiosErrorLike {
  if (axios.isAxiosError(error)) {
    return true;
  }

  return (
    isRecord(error) &&
    "response" in error &&
    (error.response === undefined || isRecord(error.response))
  );
}

export function getErrorPayload(error: unknown): ApiErrorPayload | null {
  if (!isAxiosErrorLike(error) || !isApiErrorPayload(error.response?.data)) {
    return null;
  }

  return error.response.data;
}

export function getErrorStatus(error: unknown): number | null {
  if (isAxiosErrorLike(error) && typeof error.response?.status === "number") {
    return error.response.status;
  }

  return null;
}

export function getErrorDetail(error: unknown): string | null {
  const data = getErrorPayload(error);
  if (!data) {
    return null;
  }

  const detail = data.detail;
  return typeof detail === "string" && detail.trim().length > 0 ? detail : null;
}

export function getPayloadMessage(error: unknown): string | null {
  const data = getErrorPayload(error);
  if (!data) {
    return null;
  }

  const message = data.message;
  return typeof message === "string" && message.trim().length > 0 ? message : null;
}

export function getErrorMessage(error: unknown, fallback: string): string {
  const detail = getErrorDetail(error);
  if (detail) {
    return detail;
  }

  const payloadMessage = getPayloadMessage(error);
  if (payloadMessage) {
    return payloadMessage;
  }

  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message;
  }

  return fallback;
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (typeof error === "object" &&
      error !== null &&
      "name" in error &&
      error.name === "AbortError")
  );
}
