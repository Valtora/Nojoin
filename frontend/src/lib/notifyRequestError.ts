import { getErrorDetail } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";

/**
 * Logs a failed request and shows it as an error notification.
 *
 * When the API refuses a request, its `detail` gives the reason ("Tag with this
 * name already exists"), so the notification shows it. A failure with no
 * `detail`, such as a lost connection or a 500, shows `fallback`, which names
 * the action that failed, rather than the HTTP client's own message.
 */
export function notifyRequestError(error: unknown, fallback: string): void {
  console.error(fallback, error);
  useNotificationStore.getState().addNotification({
    type: "error",
    message: getErrorDetail(error) ?? fallback,
  });
}
