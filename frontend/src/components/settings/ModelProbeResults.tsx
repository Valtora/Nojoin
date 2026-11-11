import { AlertTriangle, CheckCircle } from "lucide-react";

import type { ModelProbeResult } from "@/lib/api";
import { cn } from "@/lib/cn";

const ROLE_LABEL: Record<ModelProbeResult["role"], string> = {
  main: "Default model",
  meeting_edge: "Meeting Edge model",
};

/**
 * One line per model Test connection ran (#991): whether it answered, and if
 * not, what failed, in the server's wording, which names the model. Rendered
 * inside the caller's live region so the outcome is announced.
 */
export default function ModelProbeResults({
  results,
  className,
}: {
  results: ModelProbeResult[];
  className?: string;
}) {
  if (results.length === 0) return null;

  return (
    <ul className={cn("space-y-1 text-xs", className)} aria-label="Models tested">
      {results.map((result) => (
        <li
          key={result.model}
          data-probe-kind={result.kind}
          className={cn(
            "flex items-start gap-1.5",
            result.ok ? "text-status-success-fg" : "text-status-danger-fg",
          )}
        >
          {result.ok ? (
            <CheckCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          )}
          <span>
            <span className="font-semibold">
              {ROLE_LABEL[result.role]}
              {result.ok ? " passed" : " failed"}:
            </span>{" "}
            {result.message}
          </span>
        </li>
      ))}
    </ul>
  );
}
