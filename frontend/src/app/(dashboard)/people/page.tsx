"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Plus, Users } from "lucide-react";
import ConfirmationModal from "@/components/ConfirmationModal";
import {
  getGlobalSpeakers,
  createGlobalSpeaker,
  updateGlobalSpeaker,
  deleteGlobalSpeaker,
} from "@/lib/api";
import { GlobalSpeaker } from "@/types";
import { PeopleTable } from "@/components/people/PeopleTable";
import { PeopleFilters } from "@/components/people/PeopleFilters";
import { PersonModal } from "@/components/people/PersonModal";
import { PeopleTagSidebar } from "@/components/people/PeopleTagSidebar";
import {
  BatchEditModal,
  BatchUpdates,
} from "@/components/people/BatchEditModal";
import { Trash2, Edit2, CheckSquare } from "lucide-react";
import { deleteGlobalSpeakerEmbedding } from "@/lib/api"; // Ensure this is imported for batch voiceprint delete

export default function PeoplePage() {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [people, setPeople] = useState<GlobalSpeaker[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Selection State
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingPerson, setEditingPerson] = useState<GlobalSpeaker | null>(
    null,
  );
  const [personToDelete, setPersonToDelete] = useState<GlobalSpeaker | null>(
    null,
  );

  // Batch Edit State
  const [isBatchEditOpen, setIsBatchEditOpen] = useState(false);
  const [isBatchDeleting, setIsBatchDeleting] = useState(false);
  const [isBatchDeleteConfirmOpen, setIsBatchDeleteConfirmOpen] =
    useState(false);

  // Fetch People
  const fetchPeople = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await getGlobalSpeakers({
        q: searchQuery,
        tags: selectedTagIds.length > 0 ? selectedTagIds : undefined,
      });
      setPeople(data);
    } catch (error) {
      console.error("Failed to fetch people:", error);
    } finally {
      setIsLoading(false);
    }
  }, [searchQuery, selectedTagIds]);

  useEffect(() => {
    fetchPeople();
  }, [fetchPeople]);

  // Handlers
  const handleAddNew = () => {
    setEditingPerson(null);
    setIsModalOpen(true);
  };

  const handleEdit = (person: GlobalSpeaker) => {
    setEditingPerson(person);
    setIsModalOpen(true);
  };

  const handleDelete = (person: GlobalSpeaker) => {
    setPersonToDelete(person);
  };

  const confirmDelete = async () => {
    if (personToDelete) {
      try {
        await deleteGlobalSpeaker(personToDelete.id);
        fetchPeople();
      } catch (error) {
        console.error("Failed to delete person:", error);
      } finally {
        setPersonToDelete(null);
      }
    }
  };

  const handleSave = async (
    data: Partial<GlobalSpeaker> & { tag_ids: number[] },
  ) => {
    try {
      if (editingPerson) {
        await updateGlobalSpeaker(editingPerson.id, data);
      } else {
        await createGlobalSpeaker(data);
      }
      fetchPeople();
    } catch (error) {
      throw error;
    }
  };

  const handleToggleTag = (tagId: number) => {
    setSelectedTagIds((prev) =>
      prev.includes(tagId)
        ? prev.filter((id) => id !== tagId)
        : [...prev, tagId],
    );
  };

  // Selection Handlers
  const handleToggleSelection = (id: number) => {
    const newSet = new Set(selectedIds);
    if (newSet.has(id)) {
      newSet.delete(id);
    } else {
      newSet.add(id);
    }
    setSelectedIds(newSet);
  };

  const handleSelectAll = (select: boolean) => {
    if (select) {
      // Select all currently visible people
      const newSet = new Set(people.map((p) => p.id));
      setSelectedIds(newSet);
    } else {
      setSelectedIds(new Set());
    }
  };

  // Batch Actions
  const handleBatchDelete = () => {
    setIsBatchDeleteConfirmOpen(true);
  };

  const executeBatchDelete = async () => {
    setIsBatchDeleting(true);
    try {
      await Promise.all(
        Array.from(selectedIds).map((id) => deleteGlobalSpeaker(id)),
      );
      setSelectedIds(new Set());
      fetchPeople();
    } catch (error) {
      console.error("Batch delete failed", error);
      alert("Failed to delete some people.");
    } finally {
      setIsBatchDeleting(false);
      setIsBatchDeleteConfirmOpen(false);
    }
  };

  const handleBatchSave = async (updates: BatchUpdates) => {
    try {
      const updatePromises = Array.from(selectedIds).map(async (id) => {
        const person = people.find((p) => p.id === id);
        if (!person) return;

        // 1. Prepare data update
        const data: any = {};
        if (updates.company) data.company = updates.company;
        if (updates.title) data.title = updates.title;
        if (updates.email) data.email = updates.email;
        if (updates.phone_number) data.phone_number = updates.phone_number;

        // 2. Handle Tags
        if (updates.tags) {
          const currentTagIds = person.tags?.map((t) => t.id) || [];
          let newTagIds = [...currentTagIds];

          if (updates.tags.action === "add") {
            // Union
            const toAdd = updates.tags.tagIds;
            toAdd.forEach((tid) => {
              if (!newTagIds.includes(tid)) newTagIds.push(tid);
            });
          } else if (updates.tags.action === "remove") {
            // Difference
            const toRemove = updates.tags.tagIds;
            newTagIds = newTagIds.filter((tid) => !toRemove.includes(tid));
          } else if (updates.tags.action === "set") {
            // Replace
            newTagIds = updates.tags.tagIds;
          }

          data.tag_ids = newTagIds;
        }

        // Perform Update
        // Only call update if there are fields to change
        if (Object.keys(data).length > 0) {
          await updateGlobalSpeaker(id, data);
        }

        // 3. Handle Voiceprint Deletion
        if (updates.deleteVoiceprints && person.has_voiceprint) {
          await deleteGlobalSpeakerEmbedding(id);
        }
      });

      await Promise.all(updatePromises);
      fetchPeople();
      setSelectedIds(new Set());
    } catch (error) {
      console.error("Batch update failed:", error);
      alert("Batch update failed partially or fully.");
    }
  };

  return (
    <div className="flex h-full bg-gray-50 dark:bg-gray-900 overflow-hidden">
      {/* Sidebar */}
      <PeopleTagSidebar
        selectedTagIds={selectedTagIds}
        onToggleTag={handleToggleTag}
        onClearFilters={() => setSelectedTagIds([])}
      />

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            {/* Header Action */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
              <div>
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 flex items-center gap-3">
                  <Users className="w-8 h-8 text-orange-500" />
                  People Library
                </h1>
                <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                  Manage your contacts, speakers, and their associated details.
                </p>
              </div>
              <button
                onClick={handleAddNew}
                className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition-colors shadow-sm font-medium"
              >
                <Plus className="w-5 h-5" />
                Add Person
              </button>
            </div>

            {/* Filters */}
            <PeopleFilters onSearch={setSearchQuery} />

            {/* Batch Action Toolbar */}
            {selectedIds.size > 0 && (
              <div className="mb-4 p-3 bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 rounded-lg flex items-center justify-between animate-in fade-in slide-in-from-top-2">
                <div className="flex items-center gap-3">
                  <div className="bg-orange-100 dark:bg-orange-900/50 p-1.5 rounded-md">
                    <CheckSquare className="w-5 h-5 text-orange-600 dark:text-orange-400" />
                  </div>
                  <span className="font-medium text-orange-900 dark:text-orange-100">
                    {selectedIds.size} people selected
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setSelectedIds(new Set())}
                    className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-white dark:hover:bg-gray-800 rounded-md transition-colors"
                  >
                    Clear Selection
                  </button>
                  <div className="h-6 w-px bg-orange-200 dark:bg-orange-800 mx-1"></div>
                  <button
                    onClick={() => setIsBatchEditOpen(true)}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 border border-gray-200 dark:border-gray-700 hover:border-orange-300 rounded-md shadow-sm transition-all"
                  >
                    <Edit2 className="w-4 h-4" />
                    Edit
                  </button>
                  <button
                    onClick={handleBatchDelete}
                    disabled={isBatchDeleting}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium bg-red-600 text-white hover:bg-red-700 rounded-md shadow-sm transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete
                  </button>
                </div>
              </div>
            )}

            {/* Table */}
            <PeopleTable
              people={people}
              isLoading={isLoading}
              selectedIds={selectedIds}
              onToggleSelection={handleToggleSelection}
              onSelectAll={handleSelectAll}
              onEdit={handleEdit}
              onDelete={handleDelete}
            />
          </div>
        </main>
      </div>

      {/* Modals */}
      <PersonModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        person={editingPerson}
        onSave={handleSave}
      />

      <BatchEditModal
        isOpen={isBatchEditOpen}
        onClose={() => setIsBatchEditOpen(false)}
        selectedCount={selectedIds.size}
        onSave={handleBatchSave}
      />

      <ConfirmationModal
        isOpen={!!personToDelete}
        onClose={() => setPersonToDelete(null)}
        onConfirm={confirmDelete}
        title="Delete Person"
        message={`Are you sure you want to delete ${personToDelete?.name}? This cannot be undone.`}
        confirmText="Delete"
        isDangerous
      />

      <ConfirmationModal
        isOpen={isBatchDeleteConfirmOpen}
        onClose={() => setIsBatchDeleteConfirmOpen(false)}
        onConfirm={executeBatchDelete}
        title="Delete Selected People"
        message={`Are you sure you want to delete ${selectedIds.size} people? This cannot be undone.`}
        confirmText="Delete All"
        isDangerous
      />
    </div>
  );
}
