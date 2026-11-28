import type {
  AssistantChangePage,
  CalendarConnection,
  CalendarOAuthProvider,
  CalendarDashboardSummary,
  CalendarOverview,
  CalendarProviderConfigUpdate,
  CalendarProviderStatus,
  ProtonConnectStep,
  ProtonCredentials,
  RecordingsCalendar,
} from "@/types";
import api, { API_BASE_URL } from "./client";

export const getCalendarOverview = async (): Promise<CalendarOverview> => {
  const response = await api.get<CalendarOverview>("/calendar");
  return response.data;
};

export const getCalendarDashboardSummary = async (
  month: string,
  timeZone?: string,
): Promise<CalendarDashboardSummary> => {
  const params = new URLSearchParams();
  params.set("month", month);
  if (timeZone) {
    params.set("timezone", timeZone);
  }

  const response = await api.get<CalendarDashboardSummary>(
    `/calendar/dashboard?${params.toString()}`,
  );
  return response.data;
};

export const getRecordingsCalendar = async (
  month: string,
  timeZone?: string,
): Promise<RecordingsCalendar> => {
  const params = new URLSearchParams();
  params.set("month", month);
  if (timeZone) {
    params.set("timezone", timeZone);
  }

  const response = await api.get<RecordingsCalendar>(
    `/recordings/calendar?${params.toString()}`,
  );
  return response.data;
};

export const startCalendarAuthorisation = async (
  provider: CalendarOAuthProvider,
): Promise<{ authorisation_url: string }> => {
  const response = await api.post<{ authorisation_url: string }>(
    `/calendar/oauth/${provider}/start`,
  );
  return response.data;
};

export const getCalendarAuthorisationStartUrl = (
  provider: CalendarOAuthProvider,
): string => {
  const path = `${API_BASE_URL}/calendar/oauth/${provider}/start`;
  if (typeof window === "undefined") {
    return path;
  }
  return new URL(path, window.location.origin).toString();
};

export const updateCalendarSelection = async (
  connectionId: number,
  selectedCalendarIds: number[],
): Promise<CalendarConnection> => {
  const response = await api.put<CalendarConnection>(
    `/calendar/connections/${connectionId}/calendars`,
    { selected_calendar_ids: selectedCalendarIds },
  );
  return response.data;
};

export const updateCalendarColor = async (
  connectionId: number,
  calendarId: number,
  color: string | null,
): Promise<CalendarConnection> => {
  const response = await api.put<CalendarConnection>(
    `/calendar/connections/${connectionId}/calendars/${calendarId}/color`,
    { colour: color },
  );
  return response.data;
};

export const syncCalendarConnection = async (
  connectionId: number,
): Promise<CalendarConnection> => {
  const response = await api.post<CalendarConnection>(
    `/calendar/connections/${connectionId}/sync`,
  );
  return response.data;
};

export const disconnectCalendarConnection = async (
  connectionId: number,
): Promise<void> => {
  await api.delete(`/calendar/connections/${connectionId}`);
};

export const getCalendarProviderStatuses = async (): Promise<
  CalendarProviderStatus[]
> => {
  const response = await api.get<CalendarProviderStatus[]>(
    "/calendar/admin/providers",
  );
  return response.data;
};

export const updateCalendarProviderConfiguration = async (
  provider: CalendarOAuthProvider,
  payload: CalendarProviderConfigUpdate,
): Promise<CalendarProviderStatus> => {
  const response = await api.put<CalendarProviderStatus>(
    `/calendar/admin/providers/${provider}`,
    payload,
  );
  return response.data;
};

/** Signs in to Proton. A human check comes back as a 409 refusal. */
export const connectProton = async (
  credentials: ProtonCredentials,
  verification?: { token: string; type: string } | null,
): Promise<ProtonConnectStep> => {
  const response = await api.post<ProtonConnectStep>("/calendar/proton/connect", {
    ...credentials,
    human_verification_token: verification?.token ?? null,
    human_verification_type: verification?.type ?? null,
  });
  return response.data;
};

export const connectProtonTwoFactor = async (
  flowId: string,
  credentials: ProtonCredentials,
  code: string,
): Promise<ProtonConnectStep> => {
  const response = await api.post<ProtonConnectStep>(
    `/calendar/proton/connect/${encodeURIComponent(flowId)}/totp`,
    { ...credentials, code },
  );
  return response.data;
};

export const connectProtonUnlock = async (
  flowId: string,
  credentials: ProtonCredentials,
  mailboxPassword: string,
): Promise<ProtonConnectStep> => {
  const response = await api.post<ProtonConnectStep>(
    `/calendar/proton/connect/${encodeURIComponent(flowId)}/unlock`,
    { ...credentials, mailbox_password: mailboxPassword },
  );
  return response.data;
};

export const getAssistantChanges = async (options: {
  connectionId?: number | null;
  skip?: number;
  limit?: number;
} = {}): Promise<AssistantChangePage> => {
  const params = new URLSearchParams();
  if (options.connectionId != null) {
    params.set("connection_id", String(options.connectionId));
  }
  params.set("skip", String(options.skip ?? 0));
  params.set("limit", String(options.limit ?? 20));
  const response = await api.get<AssistantChangePage>(
    `/calendar/assistant-changes?${params.toString()}`,
  );
  return response.data;
};
