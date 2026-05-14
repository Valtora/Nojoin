export type CompanionPairingRequestState =
  | "pending"
  | "opened"
  | "completing"
  | "completed"
  | "declined"
  | "cancelled"
  | "expired"
  | "failed";

export interface CompanionPairingRequestCreateResponse {
  request_id: string;
  launch_url: string;
  status: CompanionPairingRequestState;
  expires_at: string;
  backend_origin: string;
  replacement: boolean;
}

export interface CompanionPairingRequestStatusResponse {
  request_id: string;
  status: CompanionPairingRequestState;
  expires_at: string;
  opened_at?: string | null;
  completed_at?: string | null;
  detail?: string | null;
  backend_origin: string;
  replacement: boolean;
}

interface CompanionPairingErrorResponse {
  detail?: string;
  message?: string;
  error?: string;
}

export class CompanionPairingRequestError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "CompanionPairingRequestError";
    this.status = status;
  }
}

const getCompanionApiBase = () =>
  process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/v1`
    : "https://localhost:14443/api/v1";

const readPairingError = (
  payload: CompanionPairingErrorResponse | null,
  fallback: string,
) => payload?.detail || payload?.message || payload?.error || fallback;

export const createCompanionPairingRequest = async () => {
  const response = await fetch(`${getCompanionApiBase()}/login/companion-pairing`, {
    method: "POST",
    credentials: "include",
  });
  const responseBody = (await response.json().catch(
    () => null,
  )) as CompanionPairingRequestCreateResponse | CompanionPairingErrorResponse | null;

  if (!response.ok) {
    throw new CompanionPairingRequestError(
      readPairingError(
        responseBody as CompanionPairingErrorResponse | null,
        `Failed to create companion pairing request: ${response.status}`,
      ),
      response.status,
    );
  }

  return responseBody as CompanionPairingRequestCreateResponse;
};

export const getCompanionPairingRequestStatus = async (requestId: string) => {
  const response = await fetch(
    `${getCompanionApiBase()}/login/companion-pairing/requests/${encodeURIComponent(requestId)}`,
    {
      method: "GET",
      credentials: "include",
    },
  );
  const responseBody = (await response.json().catch(
    () => null,
  )) as CompanionPairingRequestStatusResponse | CompanionPairingErrorResponse | null;

  if (!response.ok) {
    throw new CompanionPairingRequestError(
      readPairingError(
        responseBody as CompanionPairingErrorResponse | null,
        `Failed to load companion pairing request status: ${response.status}`,
      ),
      response.status,
    );
  }

  return responseBody as CompanionPairingRequestStatusResponse;
};

export const cancelCompanionPairingRequest = async (requestId: string) => {
  const response = await fetch(
    `${getCompanionApiBase()}/login/companion-pairing/requests/${encodeURIComponent(requestId)}`,
    {
      method: "DELETE",
      credentials: "include",
    },
  );
  const responseBody = (await response.json().catch(
    () => null,
  )) as CompanionPairingErrorResponse | null;

  if (!response.ok) {
    throw new CompanionPairingRequestError(
      readPairingError(
        responseBody,
        `Failed to cancel companion pairing request: ${response.status}`,
      ),
      response.status,
    );
  }

  return true;
};