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
      <div className="relative min-h-full overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.34),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.26),_transparent_36%),linear-gradient(180deg,_#ffedd5_0%,_#fff7ed_45%,_#ffe4c4_100%)] dark:bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.22),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.18),_transparent_34%),linear-gradient(180deg,_#0b1220_0%,_#0a0f1c_50%,_#0b1220_100%)]">
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.35)_0%,rgba(255,255,255,0)_55%)] dark:bg-[linear-gradient(135deg,rgba(255,255,255,0.05)_0%,rgba(255,255,255,0)_55%)]" />
        <div
          className={`relative mx-auto flex w-full flex-col px-4 md:px-8 ${paddingClassName} ${contentClassName}`}
        >
          {children}
        </div>
      </div>
    </div>
  );
}