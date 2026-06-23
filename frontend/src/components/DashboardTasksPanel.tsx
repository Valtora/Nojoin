"use client";

import { Check, Loader2 } from "lucide-react";

import { UserTask } from "@/types";

import TaskRow from "./dashboardTasks/TaskRow";
import { useDashboardTasks } from "./dashboardTasks/useDashboardTasks";
import TaskDeadlineModal from "./ui/TaskDeadlineModal";

export default function DashboardTasksPanel() {
  const {
    now,
    timeZone,
    loading,
    submitting,
    busyTaskId,
    savingTitleTaskId,
    editingTaskId,
    editingTitle,
    setEditingTitle,
    isComposerOpen,
    title,
    setTitle,
    openTasks,
    completedTasks,
    deadlineModalTask,
    isDeadlineModalSaving,
    parseTaskDeadlineForModal,
    titleInputRef,
    editingFormRef,
    editingInputRef,
    deadlineTriggerRefs,
    commitEditingTask,
    handleEditingKeyDown,
    handleComposerKeyDown,
    handleOpenComposer,
    handleCreateTask,
    handleBeginEditingTask,
    handleToggleTask,
    handleArchiveTask,
    handleDeleteTask,
    handleOpenDeadlineModal,
    handleCloseDeadlineModal,
    handleSaveDeadline,
  } = useDashboardTasks();

  const registerDeadlineTrigger = (
    taskId: number,
    node: HTMLButtonElement | null,
  ) => {
    deadlineTriggerRefs.current.set(taskId, node);
  };

  const renderTaskRow = (task: UserTask, variant: "open" | "completed") => (
    <TaskRow
      key={task.id}
      task={task}
      variant={variant}
      now={now}
      isBusy={busyTaskId === task.id || savingTitleTaskId === task.id}
      isEditing={editingTaskId === task.id}
      editingTitle={editingTitle}
      setEditingTitle={setEditingTitle}
      timeZone={timeZone}
      editingFormRef={editingFormRef}
      editingInputRef={editingInputRef}
      registerDeadlineTrigger={registerDeadlineTrigger}
      commitEditingTask={() => void commitEditingTask()}
      handleEditingKeyDown={handleEditingKeyDown}
      handleBeginEditingTask={(currentTask) =>
        void handleBeginEditingTask(currentTask)
      }
      handleToggleTask={(currentTask) => void handleToggleTask(currentTask)}
      handleArchiveTask={(currentTask) => void handleArchiveTask(currentTask)}
      handleDeleteTask={(taskId) => void handleDeleteTask(taskId)}
      handleOpenDeadlineModal={(currentTask, trigger) =>
        void handleOpenDeadlineModal(currentTask, trigger)
      }
    />
  );

  return (
    <div className="density-surface border border-orange-100 bg-white shadow-xl shadow-orange-900/10 backdrop-blur dark:border-gray-700/70 dark:bg-gray-900/85 dark:shadow-black/30">
      <div className="space-y-2">
        <div className="mt-2 flex items-start gap-3">
          <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
            <Check className="h-5 w-5" />
          </div>
          <h2 className="density-heading-section text-2xl font-semibold text-gray-950 dark:text-white">
            Task List
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
            {openTasks.length} open
          </span>
          {completedTasks.length > 0 && (
            <span className="inline-flex items-center gap-2 rounded-full border border-gray-300 bg-white/80 px-3 py-1 text-xs font-semibold text-gray-700 dark:border-gray-700 dark:bg-white/5 dark:text-gray-200">
              {completedTasks.length} completed
            </span>
          )}
        </div>
      </div>

      {isComposerOpen ? (
        <form onSubmit={handleCreateTask} className="relative mt-6">
          <input
            ref={titleInputRef}
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="Add a task and press Enter"
            disabled={submitting}
            className="h-12 w-full border-0 border-b border-orange-200 bg-transparent px-1 pr-10 text-base text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 dark:border-orange-500/20 dark:text-gray-100 dark:placeholder:text-gray-500"
          />

          {submitting && (
            <Loader2 className="pointer-events-none absolute right-1 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-orange-600 dark:text-orange-300" />
          )}
        </form>
      ) : (
        <button
          type="button"
          onClick={() => void handleOpenComposer()}
          className="mt-6 w-full border-0 border-b border-gray-300 px-1 py-3 text-left text-sm text-gray-700 transition-colors hover:border-orange-400 hover:text-gray-900 dark:border-gray-700 dark:text-gray-200 dark:hover:border-orange-500/40 dark:hover:text-white"
        >
          Add a task...
        </button>
      )}

      <div className="mt-6 space-y-6">
        {loading ? (
          <div className="density-surface-panel flex items-center gap-3 border border-gray-200 bg-white/90 px-4 py-4 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-200">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading your tasks...
          </div>
        ) : (
          <>
            {openTasks.length > 0 && (
              <div className="space-y-3">
                {openTasks.map((task) => renderTaskRow(task, "open"))}
              </div>
            )}

            {completedTasks.length > 0 && (
              <div
                className={`space-y-3 ${
                  openTasks.length > 0
                    ? "border-t border-gray-200 pt-6 dark:border-gray-700"
                    : ""
                }`}
              >
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-600 dark:text-gray-300">
                  Completed
                </div>

                {completedTasks.map((task) => renderTaskRow(task, "completed"))}
              </div>
            )}
          </>
        )}
      </div>

      <TaskDeadlineModal
        isOpen={deadlineModalTask !== null}
        taskTitle={deadlineModalTask?.title ?? ""}
        value={
          deadlineModalTask?.due_at
            ? parseTaskDeadlineForModal(deadlineModalTask.due_at)
            : null
        }
        timeZone={timeZone}
        isSaving={isDeadlineModalSaving}
        onClose={handleCloseDeadlineModal}
        onSave={handleSaveDeadline}
      />
    </div>
  );
}
