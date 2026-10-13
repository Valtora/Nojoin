import { ReactNode } from "react";

import { cn } from "@/lib/cn";

interface WorkspaceProps {
  children: ReactNode;
  wrapperClassName?: string;
  backgroundClassName?: string;
  contentClassName?: string;
  paddingClassName?: string;
}

/**
 * The scrolling page shell: a flat page surface with the density-aware
 * workspace width, gutters and gap.
 *
 * `backgroundClassName` lets the recording detail view pass `bg-transparent`
 * to opt out of the page fill entirely.
 */
export default function Workspace({
  children,
  wrapperClassName = "flex-1 overflow-auto",
  backgroundClassName = "bg-surface-page",
  contentClassName = "workspace-shell",
  paddingClassName = "workspace-pad-y",
}: WorkspaceProps) {
  return (
    <div className={wrapperClassName}>
      <div className={cn("min-h-full", backgroundClassName)}>
        <div
          className={cn(
            "workspace-pad-x mx-auto flex w-full flex-col",
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
