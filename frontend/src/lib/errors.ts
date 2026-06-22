import axios from "axios";

function getResponseData(error: unknown): Record<string, unknown> | null {
  if (
    typeof error !== "object" ||
    error === null ||
    !("response" in error) ||
    typeof error.response !== "object" ||
    error.response === null ||
    !("data" in error.response) ||
    typeof error.response.data !== "object" ||
    error.response.data === null
  ) {
    return null;
  }

  return error.response.data as Record<string, unknown>;
}

export function getErrorStatus(error: unknown): number | null {
  if (axios.isAxiosError(error)) {
    return error.response?.status ?? null;
  }

  if (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof error.response === "object" &&
    error.response !== null &&
    "status" in error.response &&
    typeof error.response.status === "number"
  ) {
    return error.response.status;
  }

  return null;
}

export function getErrorDetail(error: unknown): string | null {
  const data = getResponseData(error);
  if (!data) {
    return null;
  }

  const detail = data.detail;
  return typeof detail === "string" && detail.trim().length > 0 ? detail : null;
}

export function getErrorMessage(error: unknown, fallback: string): string {
  const detail = getErrorDetail(error);
  if (detail) {
    return detail;
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
