import { redirect } from "next/navigation";

/**
 * "Capture" was split: browser input settings and the processing defaults that
 * used to sit under Personal are now one Recording category.
 */
export default function CaptureSettingsRedirectPage() {
  redirect("/settings/recording");
}
