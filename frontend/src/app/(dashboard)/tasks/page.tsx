import TasksWorkspace from "@/components/TasksWorkspace";

export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<{ task?: string | string[] }>;
}) {
  const { task } = await searchParams;
  const taskId = typeof task === "string" && /^\d+$/.test(task) ? Number(task) : undefined;
  const initialTaskId = taskId !== undefined && Number.isSafeInteger(taskId) && taskId > 0
    ? taskId
    : undefined;
  return <TasksWorkspace initialTaskId={initialTaskId} />;
}
