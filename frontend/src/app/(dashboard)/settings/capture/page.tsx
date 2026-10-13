import { redirect } from "next/navigation";

/** Redirect for the former Capture category, now part of Recording. */
export default function CaptureSettingsRedirectPage() {
  redirect("/settings/recording");
}
