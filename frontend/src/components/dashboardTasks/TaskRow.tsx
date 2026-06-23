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
        className="inline-flex h-12 w-12 items-center justify-center rounded-l-2xl text-gray-500 transition-colors hover:bg-amber-50 hover:text-amber-700 dark:text-gray-400 dark:hover:bg-amber-500/10 dark:hover:text-amber-300"
        aria-label={`Archive ${task.title}`}
      >
        <Archive className="h-5 w-5" />
      </button>
      <button
        type="button"
        onClick={() => void handleDeleteTask(task.id)}
        disabled={isBusy}
        className="inline-flex h-12 w-12 items-center justify-center rounded-r-2xl text-gray-500 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:text-gray-400 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
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
        <Loader2 className="pointer-events-none absolute right-0 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-orange-600 dark:text-orange-300" />
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
      <div className="density-surface-subtle group grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-4 border border-gray-200 bg-gradient-to-br from-white via-white to-orange-50/50 px-4 py-4 shadow-sm shadow-orange-950/5 transition-all hover:-translate-y-0.5 hover:shadow-lg dark:border-gray-700/70 dark:from-gray-900/80 dark:via-gray-900/70 dark:to-orange-500/10">
        <button
          type="button"
          onClick={() => void handleToggleTask(task)}
          disabled={isBusy}
          className="inline-flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-gray-300 bg-white text-gray-400 transition-colors hover:border-orange-400 hover:bg-orange-50 hover:text-orange-600 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-500 dark:hover:border-orange-500/30 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
          aria-label={`Mark ${task.title} complete`}
        >
          {isBusy ? (
            <Loader2 className="h-4 w-4 animate-spin text-orange-600 dark:text-orange-300" />
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
              inputClassName="h-10 w-full border-0 border-b border-orange-200 bg-transparent px-0 pr-8 text-sm font-semibold text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 dark:border-orange-500/20 dark:text-white dark:placeholder:text-gray-500"
            />
          ) : (
            <p
              onDoubleClick={() => void handleBeginEditingTask(task)}
              className="cursor-text text-sm font-semibold text-gray-900 dark:text-white"
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
                  ? "border-solid border-orange-200 bg-orange-50/90 text-orange-900 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-100"
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
          wrapperClassName="density-surface-panel flex shrink-0 self-center border border-transparent bg-white/80 dark:bg-white/5"
        />
      </div>
    );
  }

  return (
    <div className="density-surface-subtle grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-4 border border-gray-200 bg-white/80 px-4 py-4 dark:border-gray-700 dark:bg-gray-900/60">
      <button
        type="button"
        onClick={() => void handleToggleTask(task)}
        disabled={isBusy}
        className="inline-flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-emerald-200 bg-emerald-100 text-emerald-700 transition-colors hover:border-emerald-300 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
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
            inputClassName="h-10 w-full border-0 border-b border-orange-200 bg-transparent px-0 pr-8 text-sm font-semibold text-gray-700 outline-none transition-colors placeholder:text-gray-400 focus:border-orange-500 dark:border-orange-500/20 dark:text-gray-200 dark:placeholder:text-gray-500"
          />
        ) : (
          <p
            onDoubleClick={() => void handleBeginEditingTask(task)}
            className="cursor-text text-sm font-medium text-gray-600 line-through dark:text-gray-300"
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
              className={`${DEADLINE_TRIGGER_CLASS} border-solid border-orange-200 bg-orange-50/90 text-orange-900 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-100`}
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
        wrapperClassName="flex shrink-0 self-center rounded-2xl border border-transparent bg-white/80 dark:bg-white/5"
      />
    </div>
  );
}
