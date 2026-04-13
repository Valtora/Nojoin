import { ReactNode } from "react";

interface AmbientWorkspaceProps {
  children: ReactNode;
  contentClassName?: string;
  paddingClassName?: string;
}

export default function AmbientWorkspace({
  children,
  contentClassName = "max-w-6xl gap-6",
  paddingClassName = "py-6 md:py-10",
}: AmbientWorkspaceProps) {
  return (
    <div className="flex-1 overflow-auto">
      <div className="relative min-h-full overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.22),_transparent_28%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.18),_transparent_32%),linear-gradient(180deg,_#fff7ed_0%,_#ffffff_40%,_#fffdf8_100%)] dark:bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.16),_transparent_28%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.12),_transparent_32%),linear-gradient(180deg,_#111827_0%,_#0f172a_48%,_#111827_100%)]">
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.55)_0%,rgba(255,255,255,0)_55%)] dark:bg-[linear-gradient(135deg,rgba(255,255,255,0.05)_0%,rgba(255,255,255,0)_55%)]" />
        <div
          className={`relative mx-auto flex w-full flex-col px-4 md:px-8 ${paddingClassName} ${contentClassName}`}
        >
          {children}
        </div>
      </div>
    </div>
  );
}