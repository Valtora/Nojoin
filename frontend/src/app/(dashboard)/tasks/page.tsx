import TasksWorkspace from "@/components/TasksWorkspace";

export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<{ task?: string | string[] }>;
}) {
  const taskId = Number((await searchParams).task);
  return <TasksWorkspace initialTaskId={Number.isInteger(taskId) && taskId > 0 ? taskId : undefined} />;
}
