import { ReactNode } from "react";

import { cn } from "@/lib/cn";

interface AmbientWorkspaceProps {
  children: ReactNode;
  wrapperClassName?: string;
  backgroundClassName?: string;
  contentClassName?: string;
  paddingClassName?: string;
}

export default function AmbientWorkspace({
  children,
  wrapperClassName = "flex-1 overflow-auto",
  backgroundClassName = "bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.34),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.26),_transparent_36%),linear-gradient(180deg,_#ffedd5_0%,_#fff7ed_45%,_#ffe4c4_100%)] dark:bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.22),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.18),_transparent_34%),linear-gradient(180deg,_#0b1220_0%,_#0a0f1c_50%,_#0b1220_100%)]",
  contentClassName = "workspace-shell",
  paddingClassName = "workspace-pad-y",
}: AmbientWorkspaceProps) {
  return (
    <div className={wrapperClassName}>
      <div className={`relative min-h-full overflow-hidden ${backgroundClassName}`}>
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.35)_0%,rgba(255,255,255,0)_55%)] dark:bg-[linear-gradient(135deg,rgba(255,255,255,0.05)_0%,rgba(255,255,255,0)_55%)]" />
        <div
          className={cn(
            "workspace-pad-x relative mx-auto flex w-full flex-col",
            paddingClassName,
            contentClassName,
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
