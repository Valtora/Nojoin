import { X } from "lucide-react";

import { getColorByKey } from "@/lib/constants";
import type { Tag } from "@/types";
import { ButtonBase } from "./ui/nativeBases";

/** Removable selections shared by the Recordings and Tasks tag filters. */
export default function ActiveTagFilters({
  tags,
  selectedTagIds,
  toggleTagFilter,
  clearTagFilters,
}: {
  tags: Tag[];
  selectedTagIds: number[];
  toggleTagFilter: (id: number) => void;
  clearTagFilters: () => void;
}) {
  if (selectedTagIds.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 mt-2">
      {selectedTagIds.map((tagId) => {
        const tag = tags.find((item) => item.id === tagId);
        if (!tag) return null;
        const colour = getColorByKey(tag.color);
        return (
          <div
            key={tagId}
            className="flex min-w-0 items-center gap-1.5 px-2.5 py-1 rounded-pill bg-status-neutral-bg border border-status-neutral-border text-xs group hover:border-action-border transition-colors"
          >
            <span className={`w-2 h-2 rounded-full ${colour.dot} shrink-0`} />
            <span className="truncate text-status-neutral-fg font-medium">{tag.name}</span>
            <ButtonBase
              onClick={() => toggleTagFilter(tagId)}
              className="ml-0.5 shrink-0 text-contrast-icon-muted hover:text-danger-text transition-colors"
              title={`Remove ${tag.name} filter`}
            >
              <X className="w-3 h-3" />
            </ButtonBase>
          </div>
        );
      })}
      <ButtonBase
        data-ui-hover="status-danger"
        data-ui-border="full" chrome="action"
        onClick={clearTagFilters}
        className="px-2.5 py-1 rounded-pill text-xs font-medium text-danger-text hover:bg-status-danger-bg border border-status-danger-border transition-colors"
        title="Clear all tag filters"
      >
        Clear All
      </ButtonBase>
    </div>
  );
}
