import { Egg } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ActivePersona } from "@/types";

/** The egg beside a surface's name while a holiday persona applies to it. */
export default function PersonaEgg({
  persona,
  className,
}: {
  persona?: ActivePersona | null;
  className?: string;
}) {
  if (!persona) return null;
  const label = `${persona.tooltip} ${persona.reason}`;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn("inline-flex shrink-0 text-action-text", className)}
    >
      <Egg className="h-3.5 w-3.5" aria-hidden="true" />
    </span>
  );
}
