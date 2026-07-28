import { Suspense } from "react";

import SettingsIndex from "@/components/settings/SettingsIndex";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <SettingsIndex />
    </Suspense>
  );
}
