import { getColorByKey } from "@/lib/constants";
import type { CalendarDashboardTag } from "@/types";

/** The agenda's tag chips, optionally capped for compact dashboard rows. */
export default function TagChips({
  tags = [],
  limit,
}: {
  tags?: CalendarDashboardTag[];
  limit?: number;
}) {
  if (tags.length === 0) return null;
  const visible = limit === undefined ? tags : tags.slice(0, limit);
  const hidden = tags.slice(visible.length);

  return (
    <span className={`flex min-w-0 items-center gap-1.5 ${limit === undefined ? "flex-wrap" : ""}`}>
      {visible.map((tag) => {
        const colour = getColorByKey(tag.color || "orange");
        return (
          <span
            key={tag.id}
            title={tag.name}
            className={`inline-flex min-w-0 items-center rounded-pill border border-action-border bg-action-tint px-2 py-0.5 text-[11px] font-semibold text-action-text ${limit === undefined ? "" : "max-w-32"}`}
          >
            <span className={`mr-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${colour.dot}`} />
            <span className="truncate">{tag.name}</span>
          </span>
        );
      })}
      {hidden.length > 0 && (
        <span
          className="shrink-0 text-xs text-contrast-helper"
          title={hidden.map((tag) => tag.name).join(", ")}
          aria-label={`${hidden.length} more tags: ${hidden.map((tag) => tag.name).join(", ")}`}
        >
          +{hidden.length}
        </span>
      )}
    </span>
  );
}
