import React from 'react';

import { cn } from '@/lib/cn';

interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  id?: string;
  /** The visible label's id. Without it the switch is named "Use setting". */
  'aria-labelledby'?: string;
  'aria-describedby'?: string;
}

export function Switch({
  checked,
  onCheckedChange,
  disabled,
  id,
  'aria-labelledby': labelledBy,
  'aria-describedby': describedBy,
}: SwitchProps) {
  return (
    <button
      data-ui="switch"
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      id={id}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent',
        'transition-colors duration-200 ease-in-out',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring',
        // The off state is a control boundary rather than a fill, so it takes
        // the control border colour: an unchecked switch still has to be
        // identifiable as a switch.
        checked ? 'bg-action' : 'bg-control-border',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      {!labelledBy && <span className="sr-only">Use setting</span>}
      <span
        aria-hidden="true"
        className={cn(
          'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-action-on ring-0',
          'transition duration-200 ease-in-out',
          checked ? 'translate-x-5' : 'translate-x-0',
        )}
      />
    </button>
  );
}
