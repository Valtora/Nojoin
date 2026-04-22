export const COMPANION_URL = "http://127.0.0.1:12345";

export type CompanionLocalAction =
  | "status:read"
  | "settings:read"
  | "settings:write"
  | "devices:read"
  | "waveform:read"
  | "recording:start"
  | "recording:stop"
  | "recording:pause"
  | "recording:resume"
  | "update:trigger";

interface CompanionLocalTokenResponse {
  token: string;
  expires_in: number;
}

interface CompanionLocalErrorResponse {
  detail?: string;
  message?: string;
  error?: string;
}

interface CachedCompanionToken {
  token: string;
  expiresAt: number;
}

export class CompanionLocalRequestError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "CompanionLocalRequestError";
    this.status = status;
  }
}

const TOKEN_REFRESH_SKEW_MS = 10_000;
const tokenCache = new Map<string, CachedCompanionToken>();
const tokenRequestCache = new Map<string, Promise<string>>();

const getCompanionApiBase = () =>
  process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, "")}/v1`
    : "https://localhost:14443/api/v1";

const normaliseActions = (
  actions: CompanionLocalAction | CompanionLocalAction[],
) => [...new Set(Array.isArray(actions) ? actions : [actions])].sort();

const getTokenCacheKey = (
  actions: CompanionLocalAction | CompanionLocalAction[],
) => normaliseActions(actions).join(",");

const invalidateCompanionLocalToken = (
  actions: CompanionLocalAction | CompanionLocalAction[],
) => {
  const cacheKey = getTokenCacheKey(actions);
  tokenCache.delete(cacheKey);
  tokenRequestCache.delete(cacheKey);
};

const readErrorPayload = async (response: Response, fallback: string) => {
  const payload = (await response.json().catch(
    () => null,
  )) as CompanionLocalErrorResponse | null;
  return payload?.message || payload?.detail || fallback;
};

const loadCompanionLocalToken = async (
  actions: CompanionLocalAction | CompanionLocalAction[],
  signal?: AbortSignal,
) => {
  const requestedActions = normaliseActions(actions);
  const cacheKey = requestedActions.join(",");
  const cached = tokenCache.get(cacheKey);
  if (cached && cached.expiresAt - TOKEN_REFRESH_SKEW_MS > Date.now()) {
    return cached.token;
  }

  let pending = tokenRequestCache.get(cacheKey);
  if (!pending) {
    pending = (async () => {
      const response = await fetch(`${getCompanionApiBase()}/login/companion-local-token`, {
        method: "POST",
        credentials: "include",
        signal,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ actions: requestedActions }),
      });

      if (!response.ok) {
        throw new CompanionLocalRequestError(
          await readErrorPayload(
            response,
            `Failed to fetch companion local control token: ${response.status}`,
          ),
          response.status,
        );
      }

      const payload =
        (await response.json()) as CompanionLocalTokenResponse;
      tokenCache.set(cacheKey, {
        token: payload.token,
        expiresAt: Date.now() + payload.expires_in * 1000,
      });
      return payload.token;
    })().finally(() => {
      tokenRequestCache.delete(cacheKey);
    });

    tokenRequestCache.set(cacheKey, pending);
  }

  return pending;
};

export const companionLocalFetch = async (
  path: string,
  init: RequestInit = {},
  actions: CompanionLocalAction | CompanionLocalAction[],
) => {
  const executeRequest = async () => {
    const token = await loadCompanionLocalToken(
      actions,
      init.signal ?? undefined,
    );
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);

    return fetch(`${COMPANION_URL}${path}`, {
      ...init,
      headers,
    });
  };

  let response = await executeRequest();
  if (response.status === 401) {
    invalidateCompanionLocalToken(actions);
    response = await executeRequest();
  }

  return response;
};

export const readCompanionLocalError = readErrorPayload;