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
    (isRecord(error) && error.name === "AbortError")
  );
}
