import { redirect } from "next/navigation";

export default function CaptureSettingsRedirectPage() {
  redirect("/settings?tab=capture");
}
