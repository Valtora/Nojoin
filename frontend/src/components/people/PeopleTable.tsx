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
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  if (people.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-center bg-white dark:bg-gray-800/50 rounded-xl border border-gray-200 dark:border-gray-700 border-dashed">
        <div className="w-16 h-16 bg-gray-100 dark:bg-gray-800 rounded-full flex items-center justify-center mb-4">
          <MessageSquare className="w-8 h-8 text-gray-400" />
        </div>
        <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-1">
          No people found
        </h3>
        <p className="text-gray-500 dark:text-gray-400 max-w-sm">
          No people match your search criteria. Try a different filter or add a
          new person.
        </p>
      </div>
    );
  }

  return (
    <>
      {/* Desktop / wide viewports: the full data table. */}
      <div className="hidden lg:block bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 border-b border-gray-200 dark:border-gray-700">
                <th className="px-6 py-4 w-12">
                  <input
                    type="checkbox"
                    className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4 cursor-pointer"
                    checked={allSelected}
                    ref={(input) => {
                      if (input) input.indeterminate = someSelected;
                    }}
                    onChange={(e) => onSelectAll(e.target.checked)}
                  />
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider w-1/4">
                  Name
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider w-1/4">
                  Contact
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider w-1/6">
                  Company / Role
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider w-1/12 text-center">
                  Meetings
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider w-1/6">
                  Tags
                </th>
                <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider w-20 text-right">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {people.map((person) => (
                <tr
                  key={person.id}
                  className={`group hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors cursor-context-menu ${selectedIds.has(person.id) ? "bg-orange-50 dark:bg-orange-900/10" : ""}`}
                  onContextMenu={(e) => handleContextMenu(e, person)}
                >
                  {/* Checkbox */}
                  <td
                    className="px-6 py-4"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4 cursor-pointer"
                      checked={selectedIds.has(person.id)}
                      onChange={() => onToggleSelection(person.id)}
                    />
                  </td>

                  {/* Name & Avatar */}
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div
                        className={`w-10 h-10 rounded-full flex items-center justify-center text-white text-sm font-bold shadow-sm ring-2 ring-white dark:ring-gray-800 ${person.color?.startsWith("#") ? "" : getColorByKey(person.color).dot}`}
                        style={
                          person.color?.startsWith("#")
                            ? { backgroundColor: person.color }
                            : {}
                        }
                      >
                        {person.name.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <div className="font-medium text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
                          {person.name}
                          {person.is_voiceprint_locked && (
                            <div className="group/lock relative">
                              <Lock className="w-3.5 h-3.5 text-green-500" />
                              <span className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1.5 px-2 py-1 text-xs text-white bg-gray-900 rounded opacity-0 group-hover/lock:opacity-100 transition-opacity whitespace-nowrap pointer-events-none">
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
                        <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                          <Mail className="w-3.5 h-3.5 text-gray-400" />
                          <a
                            href={`mailto:${person.email}`}
                            className="hover:text-orange-500 hover:underline"
                          >
                            {person.email}
                          </a>
                        </div>
                      )}
                      {person.phone_number && (
                        <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                          <Phone className="w-3.5 h-3.5 text-gray-400" />
                          <span>{person.phone_number}</span>
                        </div>
                      )}
                      {!person.email && !person.phone_number && (
                        <span className="text-sm text-gray-400 italic">
                          No contact info
                        </span>
                      )}
                    </div>
                  </td>

                  {/* Company / Role */}
                  <td className="px-6 py-4">
                    <div className="space-y-0.5">
                      {person.title && (
                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                          {person.title}
                        </div>
                      )}
                      {person.company && (
                        <div className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400">
                          {person.company}
                        </div>
                      )}
                      {!person.title && !person.company && (
                        <span className="text-sm text-gray-400 italic">--</span>
                      )}
                    </div>
                  </td>

                  {/* Meetings */}
                  <td className="px-6 py-4 text-center">
                    <span className="text-sm font-medium text-gray-600 dark:text-gray-400">
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
                              className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border bg-gray-100 dark:bg-gray-800 border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-200"
                            >
                              <span
                                className={`w-1.5 h-1.5 rounded-full mr-1.5 ${color.dot}`}
                              />
                              {tag.name}
                            </span>
                          );
                        })
                      ) : (
                        <span className="text-xs text-gray-400 italic">
                          No tags
                        </span>
                      )}
                    </div>
                  </td>

                  {/* Actions */}
                  <td className="px-6 py-4 text-right relative">
                    <button
                      onClick={(e) => openMenuFromButton(e, person)}
                      className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
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
        <label className="flex cursor-pointer items-center gap-2 px-1 text-sm text-gray-600 dark:text-gray-400">
          <input
            type="checkbox"
            className="h-4 w-4 cursor-pointer rounded border-gray-300 text-orange-600 focus:ring-orange-500"
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
            className={`rounded-xl border p-4 shadow-sm transition-colors ${
              selectedIds.has(person.id)
                ? "border-orange-300 bg-orange-50 dark:border-orange-500/40 dark:bg-orange-900/10"
                : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
            }`}
          >
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4 shrink-0 cursor-pointer rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                checked={selectedIds.has(person.id)}
                onChange={() => onToggleSelection(person.id)}
              />
              <div
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white shadow-sm ring-2 ring-white dark:ring-gray-800 ${person.color?.startsWith("#") ? "" : getColorByKey(person.color).dot}`}
                style={
                  person.color?.startsWith("#")
                    ? { backgroundColor: person.color }
                    : {}
                }
              >
                {person.name.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 font-medium text-gray-900 dark:text-gray-100">
                  <span className="truncate">{person.name}</span>
                  {person.is_voiceprint_locked && (
                    <Lock
                      className="h-3.5 w-3.5 shrink-0 text-green-500"
                      aria-label="Voiceprint locked"
                    />
                  )}
                </div>
                {(person.title || person.company) && (
                  <div className="truncate text-sm text-gray-500 dark:text-gray-400">
                    {[person.title, person.company].filter(Boolean).join(" · ")}
                  </div>
                )}
              </div>
              <button
                onClick={(e) => openMenuFromButton(e, person)}
                className="-mr-1 -mt-1 shrink-0 rounded-full p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                aria-label={`Actions for ${person.name}`}
              >
                <MoreVertical className="h-5 w-5" />
              </button>
            </div>

            {(person.email || person.phone_number) && (
              <div className="mt-3 space-y-1 pl-7">
                {person.email && (
                  <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                    <Mail className="h-3.5 w-3.5 shrink-0 text-gray-400" />
                    <a
                      href={`mailto:${person.email}`}
                      className="truncate hover:text-orange-500 hover:underline"
                    >
                      {person.email}
                    </a>
                  </div>
                )}
                {person.phone_number && (
                  <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                    <Phone className="h-3.5 w-3.5 shrink-0 text-gray-400" />
                    <span>{person.phone_number}</span>
                  </div>
                )}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-1.5 pl-7">
              <span className="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
                {person.recording_count || 0} meetings
              </span>
              {person.tags?.map((tag) => {
                const color = getColorByKey(tag.color || "gray");
                return (
                  <span
                    key={tag.id}
                    className="inline-flex items-center rounded-full border border-gray-300 bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
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
              className: "text-red-600",
              onClick: () => onDelete(contextMenu.person),
            },
          ]}
        />
      )}
    </>
  );
}
