"use client";

import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { cn } from "@/lib/cn";
import type { ViewType } from "@/lib/store";
import { X } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import BatchTagModal from "./BatchTagModal";
import {
  LIST_ACTIONS,
  VIEW_ACTIONS,
  recordingsPhrase,
  type ListActionId,
} from "./recordings/recordingListActions";
import Button from "./ui/Button";
import IconButton from "./ui/IconButton";
import { TOUCH_TARGET_HEIGHT, TOUCH_TARGET_SQUARE } from "./ui/touchTarget";

interface BatchActionBarProps {
  view: ViewType;
  /** How many shown rows are selected. */
  count: number;
  onClear: () => void;
  /** Every action in the view's table but Tag, which the bar asks for a tag first. */
  onAction: (action: Exclude<ListActionId, "tag">) => void;
  onTag: (tagName: string) => void;
}

/**
 * The actions on a selection, from the view's row of the action table. It
 * sits at the foot of the scrolling list rather than over it, so the last row
 * can always be scrolled clear of it.
 */
export default function BatchActionBar({
  view,
  count,
  onClear,
  onAction,
  onTag,
}: BatchActionBarProps) {
  const [isTagModalOpen, setTagModalOpen] = useState(false);

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClear();
    }
  };

  return (
    <>
      {/* Mounted with nothing selected too, so the first count is announced. */}
      <p role="status" className="sr-only">
        {count > 0 ? `${recordingsPhrase(count)} selected` : ""}
      </p>

      {count > 0 && (
        <div className="sticky bottom-0 z-[var(--z-sticky)] px-2 pb-2">
          <SurfaceBase
            data-ui-fill="float"
            data-ui-border="full"
            kind="float"
            role="group"
            aria-label="Selected recordings"
            onKeyDown={handleKeyDown}
            className="flex flex-wrap items-center gap-2 rounded-lg border border-surface-float-border bg-surface-float p-2 shadow-float"
          >
            <div className="mr-auto flex items-center gap-1">
              <span className="rounded-md bg-action-tint px-2 py-1 text-xs font-medium text-action-tint-fg">
                {count} selected
              </span>
              <IconButton
                size="sm"
                icon={<X aria-hidden="true" />}
                aria-label="Clear selection"
                title="Clear selection"
                onClick={onClear}
                className={TOUCH_TARGET_SQUARE}
              />
            </div>

            <div className="flex flex-wrap items-center gap-1">
              {VIEW_ACTIONS[view].map((id) => {
                const { label, icon: Icon, destructive } = LIST_ACTIONS[id];
                return (
                  <Button
                    key={id}
                    size="sm"
                    variant={destructive ? "danger" : "ghost"}
                    iconLeft={<Icon aria-hidden="true" className="h-4 w-4" />}
                    // Narrow enough that a view's actions share one line
                    // at the rail's default width.
                    className={cn("px-2", TOUCH_TARGET_HEIGHT)}
                    onClick={() =>
                      id === "tag" ? setTagModalOpen(true) : onAction(id)
                    }
                  >
                    {label}
                  </Button>
                );
              })}
            </div>
          </SurfaceBase>
        </div>
      )}

      <BatchTagModal
        isOpen={isTagModalOpen}
        onClose={() => setTagModalOpen(false)}
        onApply={onTag}
        count={count}
        mode="add"
      />
    </>
  );
}
