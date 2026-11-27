"use client";

import DocumentsView from "@/components/DocumentsView";
import type { RecordingId } from "@/types";

interface DocumentsSectionProps {
  active: boolean;
  recordingId: RecordingId;
  canEdit?: boolean;
  canReparse?: boolean;
}

export default function DocumentsSection({
  active,
  recordingId,
  canEdit,
  canReparse,
}: DocumentsSectionProps) {
  return (
    <div
      role="tabpanel"
      id="panel-documents"
      aria-labelledby="tab-documents"
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      <DocumentsView recordingId={recordingId} canEdit={canEdit} canReparse={canReparse} />
    </div>
  );
}
