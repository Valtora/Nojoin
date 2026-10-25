"use client";

import { InputBase } from '@/components/ui/nativeBases';
import { useState, type ReactNode } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import {
  BarChart3,
  Check,
  Download,
  FileText,
  FileType,
  Music,
  NotebookPen,
  ScrollText,
  StickyNote,
} from "lucide-react";
import Button from "./ui/Button";
import Modal from "./ui/Modal";
import type { ExportFormat, ExportSection, ExportSelection } from "@/lib/api";

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onExport: (selection: ExportSelection) => void;
  hasNotes: boolean;
  /** Whether the reader has saved notes on the timeline; without any, there is nothing to tick. */
  hasTimelineNotes: boolean;
  /** False until the meeting has been processed, which is when analytics exist. */
  hasAnalytics: boolean;
  /** False when the recording's audio is gone, so there is nothing to export. */
  hasAudio: boolean;
}

interface OptionCardProps {
  type: "checkbox" | "radio";
  name: string;
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  icon: ReactNode;
  label: string;
  description: string;
}

/** One selectable card. Selection tints the card; nothing lifts on hover. */
function OptionCard({
  type,
  name,
  checked,
  disabled = false,
  onChange,
  icon,
  label,
  description,
}: OptionCardProps) {
  return (
    <label
      className={`flex items-center gap-3 rounded-lg border-2 p-3 transition-colors ${
        disabled
          ? "cursor-not-allowed border-surface-border opacity-50"
          : checked
            ? "cursor-pointer border-action bg-action-tint"
            : "cursor-pointer border-surface-border hover:border-control-border"
      }`}
    >
      <InputBase
        type={type}
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={() => !disabled && onChange()}
        className="sr-only"
        aria-label={label}
      />
      <span
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
          checked
            ? "bg-action-tint text-action-text"
            : "bg-surface-inset text-contrast-helper"
        }`}
        aria-hidden="true"
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-foreground">{label}</span>
        <span className="block text-sm text-contrast-helper">{description}</span>
      </span>
      <span
        className={`flex h-5 w-5 shrink-0 items-center justify-center border-2 ${
          type === "radio" ? "rounded-full" : "rounded"
        } ${checked ? "border-action bg-action" : "border-control-border"}`}
        aria-hidden="true"
      >
        {checked &&
          (type === "radio" ? (
            <span className="h-2 w-2 rounded-full bg-surface-card" />
          ) : (
            <Check className="h-3.5 w-3.5 text-action-on" strokeWidth={3} />
          ))}
      </span>
    </label>
  );
}

const FORMATS: { value: ExportFormat; label: string; description: string }[] = [
  {
    value: "pdf",
    label: "PDF (.pdf)",
    description: "On the Nojoin letterhead, with charts",
  },
  {
    value: "docx",
    label: "Word (.docx)",
    description: "Editable, with the same layout and charts",
  },
  {
    value: "txt",
    label: "Text (.txt)",
    description: "Plain text, with notes kept as Markdown",
  },
];

const defaultSections = (hasNotes: boolean): ExportSection[] =>
  hasNotes ? ["notes"] : ["transcript"];

/** Where the reader's notes would go in a file of these sections. */
const myNotesDescription = (notes: boolean, transcript: boolean): string => {
  if (notes && transcript) {
    return "Your notes in the transcript, and the User Notes that end the meeting notes";
  }
  if (transcript) return "Your notes, each where it falls in the transcript";
  if (notes) return "The User Notes that end the meeting notes";
  return "Tick Meeting notes or Transcript to include them";
};

export default function ExportModal({
  isOpen,
  onClose,
  onExport,
  hasNotes,
  hasTimelineNotes,
  hasAnalytics,
  hasAudio,
}: ExportModalProps) {
  const [sections, setSections] = useState<ExportSection[]>(() =>
    defaultSections(hasNotes),
  );
  const [format, setFormat] = useState<ExportFormat>("pdf");
  const [includeNotes, setIncludeNotes] = useState(true);
  const hasNotesSection = sections.includes("notes");
  const hasTranscript = sections.includes("transcript");
  // The reader's notes sit in the transcript, and generated notes end with
  // them under User Notes, so the tick governs both.
  const carriesMyNotes = hasNotesSection || hasTranscript;

  // Start each opening from what this recording can actually export.
  useOnDepsChange([isOpen], () => {
    if (isOpen) {
      setSections(defaultSections(hasNotes));
      setIncludeNotes(true);
    }
  });

  const toggle = (section: ExportSection) =>
    setSections((current) =>
      current.includes(section)
        ? current.filter((value) => value !== section)
        : [...current, section],
    );

  const handleExport = () => {
    onExport({ kind: "document", sections, format, includeNotes });
    onClose();
  };

  const handleAudio = () => {
    onExport({ kind: "audio" });
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="sm"
      title="Export"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleExport}
            disabled={sections.length === 0}
            iconLeft={<Download aria-hidden="true" className="h-4 w-4" />}
          >
            Export
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <fieldset className="space-y-2">
          <legend className="mb-2 text-sm text-contrast-helper">
            Include any of these, in one file:
          </legend>
          <OptionCard
            type="checkbox"
            name="exportSections"
            checked={sections.includes("notes")}
            disabled={!hasNotes}
            onChange={() => toggle("notes")}
            icon={<StickyNote className="h-5 w-5" />}
            label="Meeting notes"
            description={
              hasNotes
                ? "The AI-generated meeting notes"
                : "No notes available - generate notes first"
            }
          />
          <OptionCard
            type="checkbox"
            name="exportSections"
            checked={sections.includes("analytics")}
            disabled={!hasAnalytics}
            onChange={() => toggle("analytics")}
            icon={<BarChart3 className="h-5 w-5" />}
            label="Analytics"
            description={
              hasAnalytics
                ? "Who spoke, how the conversation moved and what was discussed"
                : "Available once the meeting has finished processing"
            }
          />
          <OptionCard
            type="checkbox"
            name="exportSections"
            checked={sections.includes("transcript")}
            onChange={() => toggle("transcript")}
            icon={<ScrollText className="h-5 w-5" />}
            label="Transcript"
            description="The diarized transcript with timestamps"
          />
          {hasTimelineNotes ? (
            <OptionCard
              type="checkbox"
              name="exportTimelineNotes"
              checked={carriesMyNotes && includeNotes}
              disabled={!carriesMyNotes}
              onChange={() => setIncludeNotes((current) => !current)}
              icon={<NotebookPen className="h-5 w-5" />}
              label="Include my notes"
              description={myNotesDescription(hasNotesSection, hasTranscript)}
            />
          ) : null}
        </fieldset>

        <fieldset className="space-y-2 border-t border-surface-border pt-4">
          <legend className="sr-only">Format</legend>
          <p className="mb-2 text-sm text-contrast-helper">Format:</p>
          {FORMATS.map((option) => (
            <OptionCard
              key={option.value}
              type="radio"
              name="exportFormat"
              checked={format === option.value}
              onChange={() => setFormat(option.value)}
              icon={
                option.value === "txt" ? (
                  <FileText className="h-5 w-5" />
                ) : (
                  <FileType className="h-5 w-5" />
                )
              }
              label={option.label}
              description={option.description}
            />
          ))}
        </fieldset>

        <div className="flex items-center gap-3 border-t border-surface-border pt-4">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-inset text-contrast-helper"
            aria-hidden="true"
          >
            <Music className="h-5 w-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-medium text-foreground">
              Audio file (.mp3)
            </span>
            <span className="block text-sm text-contrast-helper">
              {hasAudio
                ? "The recording's audio, on its own"
                : "This recording's audio is not available"}
            </span>
          </span>
          <Button variant="secondary" onClick={handleAudio} disabled={!hasAudio}>
            Export audio
          </Button>
        </div>
      </div>
    </Modal>
  );
}
