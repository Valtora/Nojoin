import type {
  CalendarConnection,
  CalendarDashboardSummary,
  CalendarOverview,
  CalendarProvider,
  CalendarProviderConfigUpdate,
  CalendarProviderStatus,
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
  provider: CalendarProvider,
): Promise<{ authorisation_url: string }> => {
  const response = await api.post<{ authorisation_url: string }>(
    `/calendar/oauth/${provider}/start`,
  );
  return response.data;
};

export const getCalendarAuthorisationStartUrl = (
  provider: CalendarProvider,
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
  provider: CalendarProvider,
  payload: CalendarProviderConfigUpdate,
): Promise<CalendarProviderStatus> => {
  const response = await api.put<CalendarProviderStatus>(
    `/calendar/admin/providers/${provider}`,
    payload,
  );
  return response.data;
};
