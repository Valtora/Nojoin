"use client";

import DocumentsView from "@/components/DocumentsView";
import type { RecordingId } from "@/types";

interface DocumentsSectionProps {
  active: boolean;
  recordingId: RecordingId;
}

export default function DocumentsSection({
  active,
  recordingId,
}: DocumentsSectionProps) {
  return (
    <div
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      <DocumentsView recordingId={recordingId} />
    </div>
  );
}
