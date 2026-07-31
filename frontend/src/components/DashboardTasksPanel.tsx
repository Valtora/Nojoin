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
    <div className="density-surface flex h-full min-h-0 flex-col border border-action-border bg-surface-card shadow-card">
      {/* One row: glyph, title, counts. This was an icon chip, a text-2xl
          heading and a separate pill row stacked vertically, which spent about
          140px before the first task. The counts belong beside the title
          rather than under it, because they qualify it. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Check className="h-5 w-5 shrink-0 text-action-text" />
        <h2 className="text-base font-semibold text-foreground">Task List</h2>
        <span className="inline-flex items-center gap-2 rounded-full border border-action-border bg-action-tint px-2.5 py-0.5 text-xs font-semibold text-action-text">
          {openTasks.length} open
        </span>
        {completedTasks.length > 0 && (
          <span className="inline-flex items-center gap-2 rounded-full border border-control-border px-2.5 py-0.5 text-xs font-semibold text-contrast-muted">
            {completedTasks.length} completed
          </span>
        )}
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
            className="h-12 w-full border-0 border-b border-action-border bg-transparent px-1 pr-10 text-base text-foreground outline-none transition-colors placeholder:text-contrast-icon-muted focus:border-action"
          />

          {submitting && (
            <Loader2 className="pointer-events-none absolute right-1 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-action-text" />
          )}
        </form>
      ) : (
        <button
          type="button"
          onClick={() => void handleOpenComposer()}
          className="mt-6 w-full border-0 border-b border-control-border px-1 py-3 text-left text-sm text-contrast-muted transition-colors hover:border-action-border hover:text-foreground"
        >
          Add a task...
        </button>
      )}

      {/* The list is what gives when the column is taller than this card's
          content: it takes the slack, and scrolls inside itself once there is
          more of it than the column has room for. Everything above it keeps
          its natural height, so the header and composer never scroll away. */}
      <div className="mt-6 min-h-0 flex-1 space-y-6 overflow-y-auto">
        {loading ? (
          <div className="density-surface-panel flex items-center gap-3 bg-surface-inset px-4 py-4 text-sm text-contrast-muted">
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
                    ? "border-t border-surface-border pt-6"
                    : ""
                }`}
              >
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-contrast-helper">
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
