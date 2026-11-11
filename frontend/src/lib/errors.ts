import axios from "axios";
import type { AxiosError } from "axios";

/**
 * Reusable runtime type guards for error and unknown-data narrowing.
 *
 * These exist so call sites stop re-implementing `typeof x === "object" &&
 * x !== null` inline and can narrow `unknown` (from `catch (error: unknown)` or
 * untyped JSON) through a single, tested surface.
 */

/** Narrows `unknown` to a plain, non-array object with string keys. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Narrows `unknown` to an Axios error, re-exported for a single import site. */
export function isAxiosError(error: unknown): error is AxiosError {
  return axios.isAxiosError(error);
}

/** Shape of the JSON error body returned by the backend API. */
export interface ApiErrorPayload {
  detail?: unknown;
}

/** Narrows the `response.data` body of an HTTP error to an API error payload. */
export function isApiErrorPayload(value: unknown): value is ApiErrorPayload {
  return isRecord(value);
}

function getResponseData(error: unknown): Record<string, unknown> | null {
  if (
    !isRecord(error) ||
    !("response" in error) ||
    !isRecord(error.response) ||
    !("data" in error.response) ||
    !isRecord(error.response.data)
  ) {
    return null;
  }

  return error.response.data;
}

export function getErrorStatus(error: unknown): number | null {
  if (isAxiosError(error)) {
    return error.response?.status ?? null;
  }

  if (
    isRecord(error) &&
    isRecord(error.response) &&
    typeof error.response.status === "number"
  ) {
    return error.response.status;
  }

  return null;
}

/** One entry of a request validation error (a 422), after the field it names. */
function validationIssueText(issue: unknown): string | null {
  if (!isRecord(issue) || typeof issue.msg !== "string" || !issue.msg.trim()) {
    return null;
  }
  const field = Array.isArray(issue.loc) ? issue.loc[issue.loc.length - 1] : undefined;
  return typeof field === "string" ? `${field.replace(/_/g, " ")}: ${issue.msg}` : issue.msg;
}

/**
 * The readable text of an API error's `detail`, whatever its shape: the text
 * itself, the `message` of a coded refusal (`{code, message}`), or the
 * messages of a request validation error's list, each after its field. Null
 * when the detail holds no text, so a caller falls back to its own message
 * instead of handing an object to something that renders it.
 */
export function describeErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail.trim().length > 0 ? detail : null;
  }
  if (Array.isArray(detail)) {
    const issues = detail
      .map(validationIssueText)
      .filter((text): text is string => text !== null);
    return issues.length > 0 ? issues.join(" ") : null;
  }
  if (isRecord(detail) && typeof detail.message === "string" && detail.message.trim()) {
    return detail.message;
  }
  return null;
}

export function getErrorDetail(error: unknown): string | null {
  const data = getResponseData(error);
  return data ? describeErrorDetail(data.detail) : null;
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
    (isRecord(error) && error.name === "AbortError")
  );
}
