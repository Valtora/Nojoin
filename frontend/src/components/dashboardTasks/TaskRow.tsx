import { Archive, Calendar, Check, Loader2, Trash2 } from "lucide-react";

import { UserTask } from "@/types";

import {
  DEADLINE_TRIGGER_CLASS,
  getDeadlineTriggerLabel,
  getTimeRemainingState,
  parseTaskDeadline,
} from "./taskUtils";

interface TaskRowCallbacks {
  isBusy: boolean;
  isEditing: boolean;
  editingTitle: string;
  setEditingTitle: (value: string) => void;
  timeZone: string;
  editingFormRef: React.RefObject<HTMLFormElement | null>;
  editingInputRef: React.RefObject<HTMLInputElement | null>;
  registerDeadlineTrigger: (
    taskId: number,
    node: HTMLButtonElement | null,
  ) => void;
  commitEditingTask: () => void;
  handleEditingKeyDown: (event: React.KeyboardEvent<HTMLInputElement>) => void;
  handleBeginEditingTask: (task: UserTask) => void;
  handleToggleTask: (task: UserTask) => void;
  handleArchiveTask: (task: UserTask) => void;
  handleDeleteTask: (taskId: number) => void;
  handleOpenDeadlineModal: (
    task: UserTask,
    trigger: HTMLButtonElement | null,
  ) => void;
}

interface TaskRowProps extends TaskRowCallbacks {
  task: UserTask;
  variant: "open" | "completed";
  now: Date;
}

function TaskActionButtons({
  task,
  isBusy,
  handleArchiveTask,
  handleDeleteTask,
  wrapperClassName,
}: {
  task: UserTask;
  isBusy: boolean;
  handleArchiveTask: (task: UserTask) => void;
  handleDeleteTask: (taskId: number) => void;
  wrapperClassName: string;
}) {
  return (
    <div className={wrapperClassName}>
      <button
        type="button"
        onClick={() => void handleArchiveTask(task)}
        disabled={isBusy}
        className="inline-flex h-12 w-12 items-center justify-center rounded-l-2xl text-contrast-helper transition-colors hover:bg-status-warning-bg hover:text-status-warning-fg"
        aria-label={`Archive ${task.title}`}
      >
        <Archive className="h-5 w-5" />
      </button>
      <button
        type="button"
        onClick={() => void handleDeleteTask(task.id)}
        disabled={isBusy}
        className="inline-flex h-12 w-12 items-center justify-center rounded-r-2xl text-contrast-helper transition-colors hover:bg-status-danger-bg hover:text-status-danger-fg"
        aria-label={`Delete ${task.title}`}
      >
        <Trash2 className="h-5 w-5" />
      </button>
    </div>
  );
}

function TaskEditForm({
  isBusy,
  editingTitle,
  setEditingTitle,
  editingFormRef,
  editingInputRef,
  commitEditingTask,
  handleEditingKeyDown,
  inputClassName,
}: {
  isBusy: boolean;
  editingTitle: string;
  setEditingTitle: (value: string) => void;
  editingFormRef: React.RefObject<HTMLFormElement | null>;
  editingInputRef: React.RefObject<HTMLInputElement | null>;
  commitEditingTask: () => void;
  handleEditingKeyDown: (event: React.KeyboardEvent<HTMLInputElement>) => void;
  inputClassName: string;
}) {
  return (
    <form
      ref={editingFormRef}
      onSubmit={(event) => {
        event.preventDefault();
        void commitEditingTask();
      }}
      className="relative"
    >
      <input
        ref={editingInputRef}
        type="text"
        value={editingTitle}
        onChange={(event) => setEditingTitle(event.target.value)}
        onKeyDown={handleEditingKeyDown}
        disabled={isBusy}
        className={inputClassName}
      />

      {isBusy && (
        <Loader2 className="pointer-events-none absolute right-0 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-action-text" />
      )}
    </form>
  );
}

export default function TaskRow(props: TaskRowProps) {
  const {
    task,
    variant,
    now,
    isBusy,
    isEditing,
    editingTitle,
    setEditingTitle,
    timeZone,
    editingFormRef,
    editingInputRef,
    registerDeadlineTrigger,
    commitEditingTask,
    handleEditingKeyDown,
    handleBeginEditingTask,
    handleToggleTask,
    handleArchiveTask,
    handleDeleteTask,
    handleOpenDeadlineModal,
  } = props;

  const deadline = task.due_at ? parseTaskDeadline(task.due_at) : null;

  if (variant === "open") {
    const timeRemainingState = getTimeRemainingState(task, now);

    return (
      <div className="density-surface-subtle group grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-4 gap-y-3 border border-surface-border bg-surface-card px-4 py-4 shadow-card transition-colors hover:border-action-border hover:bg-action-tint sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:gap-4">
        <button
          type="button"
          onClick={() => void handleToggleTask(task)}
          disabled={isBusy}
          className="inline-flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-control-border bg-surface-card text-contrast-icon-muted transition-colors hover:border-action-border hover:bg-action-tint hover:text-action-text"
          aria-label={`Mark ${task.title} complete`}
        >
          {isBusy ? (
            <Loader2 className="h-4 w-4 animate-spin text-action-text" />
          ) : (
            <Check className="h-4 w-4" />
          )}
        </button>

        <div className="min-w-0 flex-1">
          {isEditing ? (
            <TaskEditForm
              isBusy={isBusy}
              editingTitle={editingTitle}
              setEditingTitle={setEditingTitle}
              editingFormRef={editingFormRef}
              editingInputRef={editingInputRef}
              commitEditingTask={commitEditingTask}
              handleEditingKeyDown={handleEditingKeyDown}
              inputClassName="h-10 w-full border-0 border-b border-action-border bg-transparent px-0 pr-8 text-sm font-semibold text-foreground outline-none transition-colors placeholder:text-contrast-icon-muted focus:border-action"
            />
          ) : (
            <p
              onDoubleClick={() => void handleBeginEditingTask(task)}
              className="cursor-text text-sm font-semibold text-foreground"
              title="Double-click to edit"
            >
              {task.title}
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {timeRemainingState && (
              <span
                className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${timeRemainingState.className}`}
              >
                {timeRemainingState.label}
              </span>
            )}

            <button
              type="button"
              ref={(node) => {
                registerDeadlineTrigger(task.id, node);
              }}
              onClick={(event) =>
                void handleOpenDeadlineModal(task, event.currentTarget)
              }
              disabled={isBusy}
              className={
                `${DEADLINE_TRIGGER_CLASS} ` +
                (deadline
                  ? "border-solid border-action-border bg-action-tint text-action-text"
                  : "")
              }
              aria-label={`${deadline ? "Edit" : "Add"} deadline for ${task.title}`}
            >
              <span className="truncate">
                {getDeadlineTriggerLabel(deadline, timeZone)}
              </span>
              <Calendar className="h-4 w-4 shrink-0 opacity-60" />
            </button>
          </div>
        </div>

        <TaskActionButtons
          task={task}
          isBusy={isBusy}
          handleArchiveTask={handleArchiveTask}
          handleDeleteTask={handleDeleteTask}
          wrapperClassName="density-surface-panel col-span-2 justify-self-end flex shrink-0 self-center border border-transparent bg-surface-card sm:col-span-1"
        />
      </div>
    );
  }

  return (
    <div className="density-surface-subtle grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-4 gap-y-3 border border-surface-border bg-surface-card px-4 py-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:gap-4">
      <button
        type="button"
        onClick={() => void handleToggleTask(task)}
        disabled={isBusy}
        className="inline-flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-status-success-border bg-status-success-bg text-status-success-fg transition-colors hover:border-status-success-border"
        aria-label={`Mark ${task.title} incomplete`}
      >
        {isBusy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Check className="h-4 w-4" />
        )}
      </button>

      <div className="min-w-0 flex-1">
        {isEditing ? (
          <TaskEditForm
            isBusy={isBusy}
            editingTitle={editingTitle}
            setEditingTitle={setEditingTitle}
            editingFormRef={editingFormRef}
            editingInputRef={editingInputRef}
            commitEditingTask={commitEditingTask}
            handleEditingKeyDown={handleEditingKeyDown}
            inputClassName="h-10 w-full border-0 border-b border-action-border bg-transparent px-0 pr-8 text-sm font-semibold text-contrast-muted outline-none transition-colors placeholder:text-contrast-icon-muted focus:border-action"
          />
        ) : (
          <p
            onDoubleClick={() => void handleBeginEditingTask(task)}
            className="cursor-text text-sm font-medium text-contrast-helper line-through"
            title="Double-click to edit"
          >
            {task.title}
          </p>
        )}

        {deadline && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              ref={(node) => {
                registerDeadlineTrigger(task.id, node);
              }}
              onClick={(event) =>
                void handleOpenDeadlineModal(task, event.currentTarget)
              }
              disabled={isBusy}
              className={`${DEADLINE_TRIGGER_CLASS} border-solid border-action-border bg-action-tint text-action-tint-fg`}
              aria-label={`Edit deadline for ${task.title}`}
            >
              <span className="truncate">
                {getDeadlineTriggerLabel(deadline, timeZone)}
              </span>
              <Calendar className="h-4 w-4 shrink-0 opacity-60" />
            </button>
          </div>
        )}
      </div>

      <TaskActionButtons
        task={task}
        isBusy={isBusy}
        handleArchiveTask={handleArchiveTask}
        handleDeleteTask={handleDeleteTask}
        wrapperClassName="col-span-2 justify-self-end flex shrink-0 self-center rounded-2xl border border-transparent bg-surface-card sm:col-span-1"
      />
    </div>
  );
}
