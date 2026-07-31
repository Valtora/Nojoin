"use client";

import React, { useState } from "react";
import { GlobalSpeaker } from "@/types";
import { MoreVertical, Mail, Phone, MessageSquare, Lock } from "lucide-react";
import { getColorByKey } from "@/lib/constants";
import ContextMenu from "@/components/ContextMenu";

interface PeopleTableProps {
  people: GlobalSpeaker[];
  isLoading: boolean;
  selectedIds: Set<number>;
  onToggleSelection: (id: number) => void;
  onSelectAll: (selected: boolean) => void;
  onEdit: (person: GlobalSpeaker) => void;
  onDelete: (person: GlobalSpeaker) => void;
  onRecalibrate?: (person: GlobalSpeaker) => void;
  onSplit?: (person: GlobalSpeaker) => void;
}

export function PeopleTable({
  people,
  isLoading,
  selectedIds,
  onToggleSelection,
  onSelectAll,
  onEdit,
  onDelete,
  onRecalibrate,
  onSplit,
}: PeopleTableProps) {
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    person: GlobalSpeaker;
  } | null>(null);

  const allSelected =
    people.length > 0 && people.every((p) => selectedIds.has(p.id));
  const someSelected =
    people.some((p) => selectedIds.has(p.id)) && !allSelected;

  const handleContextMenu = (e: React.MouseEvent, person: GlobalSpeaker) => {
    e.preventDefault();
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      person,
    });
  };

  // Anchors the action menu to the trigger button's rectangle; ContextMenu
  // shifts itself away from the viewport edge. Shared by the table row and the
  // mobile card so both open the same menu.
  const openMenuFromButton = (e: React.MouseEvent, person: GlobalSpeaker) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    setContextMenu({ x: rect.right, y: rect.bottom, person });
  };

  if (isLoading) {
    return (
      <div className="w-full flex justify-center p-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-action"></div>
      </div>
    );
  }

  if (people.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-center bg-surface-card rounded-xl border border-surface-border border-dashed">
        <div className="w-16 h-16 bg-surface-inset rounded-full flex items-center justify-center mb-4">
          <MessageSquare className="w-8 h-8 text-contrast-icon-muted" />
        </div>
        <h3 className="text-lg font-medium text-foreground mb-1">
          No people found
        </h3>
        <p className="text-contrast-helper max-w-sm">
          No people match your search criteria. Try a different filter or add a
          new person.
        </p>
      </div>
    );
  }

  return (
    <>
      {/* Desktop / wide viewports: the full data table. */}
      <div className="hidden lg:block bg-surface-card rounded-xl shadow-card border border-surface-border">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-surface-inset border-b border-surface-border">
                <th className="px-6 py-4 w-12">
                  <input
                    type="checkbox"
                    className="rounded border-control-border text-action-text focus:ring-action w-4 h-4 cursor-pointer"
                    checked={allSelected}
                    ref={(input) => {
                      if (input) input.indeterminate = someSelected;
                    }}
                    onChange={(e) => onSelectAll(e.target.checked)}
                  />
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-contrast-helper uppercase tracking-wider w-1/4">
                  Name
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-contrast-helper uppercase tracking-wider w-1/4">
                  Contact
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-contrast-helper uppercase tracking-wider w-1/6">
                  Company / Role
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-contrast-helper uppercase tracking-wider w-1/12 text-center">
                  Meetings
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-contrast-helper uppercase tracking-wider w-1/6">
                  Tags
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-contrast-helper uppercase tracking-wider w-20 text-right">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border">
              {people.map((person) => (
                <tr
                  key={person.id}
                  className={`group hover:bg-surface-inset/50 transition-colors cursor-context-menu ${selectedIds.has(person.id) ? "bg-action-tint" : ""}`}
                  onContextMenu={(e) => handleContextMenu(e, person)}
                >
                  {/* Checkbox */}
                  <td
                    className="px-6 py-4"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      className="rounded border-control-border text-action-text focus:ring-action w-4 h-4 cursor-pointer"
                      checked={selectedIds.has(person.id)}
                      onChange={() => onToggleSelection(person.id)}
                    />
                  </td>

                  {/* Name & Avatar */}
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div
                        className={`w-10 h-10 rounded-full flex items-center justify-center text-white text-sm font-bold shadow-card ring-2 ring-surface-card ${person.color?.startsWith("#") ? "" : getColorByKey(person.color).dot}`}
                        style={
                          person.color?.startsWith("#")
                            ? { backgroundColor: person.color }
                            : {}
                        }
                      >
                        {person.name.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <div className="font-medium text-foreground flex items-center gap-1.5">
                          {person.name}
                          {person.is_voiceprint_locked && (
                            <div className="group/lock relative">
                              <Lock className="w-3.5 h-3.5 text-status-success-fg" />
                              <span className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1.5 px-2 py-1 text-xs text-foreground bg-surface-card rounded opacity-0 group-hover/lock:opacity-100 transition-opacity whitespace-nowrap pointer-events-none">
                                Voiceprint Locked
                              </span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </td>

                  {/* Contact */}
                  <td className="px-6 py-4">
                    <div className="space-y-1">
                      {person.email && (
                        <div className="flex items-center gap-2 text-sm text-contrast-helper">
                          <Mail className="w-3.5 h-3.5 text-contrast-icon-muted" />
                          <a
                            href={`mailto:${person.email}`}
                            className="hover:text-action-text hover:underline"
                          >
                            {person.email}
                          </a>
                        </div>
                      )}
                      {person.phone_number && (
                        <div className="flex items-center gap-2 text-sm text-contrast-helper">
                          <Phone className="w-3.5 h-3.5 text-contrast-icon-muted" />
                          <span>{person.phone_number}</span>
                        </div>
                      )}
                      {!person.email && !person.phone_number && (
                        <span className="text-sm text-contrast-icon-muted italic">
                          No contact info
                        </span>
                      )}
                    </div>
                  </td>

                  {/* Company / Role */}
                  <td className="px-6 py-4">
                    <div className="space-y-0.5">
                      {person.title && (
                        <div className="text-sm font-medium text-foreground">
                          {person.title}
                        </div>
                      )}
                      {person.company && (
                        <div className="flex items-center gap-1.5 text-sm text-contrast-helper">
                          {person.company}
                        </div>
                      )}
                      {!person.title && !person.company && (
                        <span className="text-sm text-contrast-icon-muted italic">--</span>
                      )}
                    </div>
                  </td>

                  {/* Meetings */}
                  <td className="px-6 py-4 text-center">
                    <span className="text-sm font-medium text-contrast-helper">
                      {person.recording_count || 0}
                    </span>
                  </td>

                  {/* Tags */}
                  <td className="px-6 py-4">
                    <div className="flex flex-wrap gap-1.5">
                      {person.tags && person.tags.length > 0 ? (
                        person.tags.map((tag) => {
                          const color = getColorByKey(tag.color || "gray");
                          return (
                            <span
                              key={tag.id}
                              className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border bg-surface-inset border-control-border text-contrast-muted"
                            >
                              <span
                                className={`w-1.5 h-1.5 rounded-full mr-1.5 ${color.dot}`}
                              />
                              {tag.name}
                            </span>
                          );
                        })
                      ) : (
                        <span className="text-xs text-contrast-icon-muted italic">
                          No tags
                        </span>
                      )}
                    </div>
                  </td>

                  {/* Actions */}
                  <td className="px-6 py-4 text-right relative">
                    <button
                      onClick={(e) => openMenuFromButton(e, person)}
                      className="p-2 text-contrast-icon-muted hover:text-contrast-helper rounded-full hover:bg-surface-inset transition-colors"
                    >
                      <MoreVertical className="w-5 h-5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Narrow viewports: stacked cards so nothing needs horizontal scrolling. */}
      <div className="lg:hidden space-y-3">
        <label className="flex cursor-pointer items-center gap-2 px-1 text-sm text-contrast-helper">
          <input
            type="checkbox"
            className="h-4 w-4 cursor-pointer rounded border-control-border text-action-text focus:ring-action"
            checked={allSelected}
            ref={(input) => {
              if (input) input.indeterminate = someSelected;
            }}
            onChange={(e) => onSelectAll(e.target.checked)}
          />
          Select all ({people.length})
        </label>

        {people.map((person) => (
          <div
            key={person.id}
            onContextMenu={(e) => handleContextMenu(e, person)}
            className={`rounded-xl border p-4 shadow-card transition-colors ${
              selectedIds.has(person.id)
                ? "border-action-border bg-action-tint"
                : "border-surface-border bg-surface-card"
            }`}
          >
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4 shrink-0 cursor-pointer rounded border-control-border text-action-text focus:ring-action"
                checked={selectedIds.has(person.id)}
                onChange={() => onToggleSelection(person.id)}
              />
              <div
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white shadow-card ring-2 ring-surface-card ${person.color?.startsWith("#") ? "" : getColorByKey(person.color).dot}`}
                style={
                  person.color?.startsWith("#")
                    ? { backgroundColor: person.color }
                    : {}
                }
              >
                {person.name.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 font-medium text-foreground">
                  <span className="truncate">{person.name}</span>
                  {person.is_voiceprint_locked && (
                    <Lock
                      className="h-3.5 w-3.5 shrink-0 text-status-success-fg"
                      aria-label="Voiceprint locked"
                    />
                  )}
                </div>
                {(person.title || person.company) && (
                  <div className="truncate text-sm text-contrast-helper">
                    {[person.title, person.company].filter(Boolean).join(" · ")}
                  </div>
                )}
              </div>
              <button
                onClick={(e) => openMenuFromButton(e, person)}
                className="-mr-1 -mt-1 shrink-0 rounded-full p-2 text-contrast-icon-muted transition-colors hover:bg-surface-inset hover:text-contrast-helper"
                aria-label={`Actions for ${person.name}`}
              >
                <MoreVertical className="h-5 w-5" />
              </button>
            </div>

            {(person.email || person.phone_number) && (
              <div className="mt-3 space-y-1 pl-7">
                {person.email && (
                  <div className="flex items-center gap-2 text-sm text-contrast-helper">
                    <Mail className="h-3.5 w-3.5 shrink-0 text-contrast-icon-muted" />
                    <a
                      href={`mailto:${person.email}`}
                      className="truncate hover:text-action-text hover:underline"
                    >
                      {person.email}
                    </a>
                  </div>
                )}
                {person.phone_number && (
                  <div className="flex items-center gap-2 text-sm text-contrast-helper">
                    <Phone className="h-3.5 w-3.5 shrink-0 text-contrast-icon-muted" />
                    <span>{person.phone_number}</span>
                  </div>
                )}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-1.5 pl-7">
              <span className="inline-flex items-center rounded-full bg-surface-inset px-2.5 py-0.5 text-xs font-medium text-contrast-helper">
                {person.recording_count || 0} meetings
              </span>
              {person.tags?.map((tag) => {
                const color = getColorByKey(tag.color || "gray");
                return (
                  <span
                    key={tag.id}
                    className="inline-flex items-center rounded-full border border-control-border bg-surface-inset px-2.5 py-0.5 text-xs font-medium text-contrast-muted"
                  >
                    <span
                      className={`mr-1.5 h-1.5 w-1.5 rounded-full ${color.dot}`}
                    />
                    {tag.name}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            {
              label: "Edit Details",
              onClick: () => onEdit(contextMenu.person),
            },
            ...(contextMenu.person.has_voiceprint && onRecalibrate
              ? [
                  {
                    label: "Recalibrate Voiceprint",
                    onClick: () => onRecalibrate(contextMenu.person),
                  },
                ]
              : []),
            {
              label: "Split / Unmerge Speaker",
              onClick: () => onSplit && onSplit(contextMenu.person),
            },
            {
              label: "Delete Person",
              className: "text-status-danger-fg",
              onClick: () => onDelete(contextMenu.person),
            },
          ]}
        />
      )}
    </>
  );
}
