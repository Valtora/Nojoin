'use client';

import * as React from 'react';
import { X, Check, ChevronDown } from 'lucide-react';

export interface Option {
    value: number | string;
    label: string;
    color?: string; // Optional color for the tag
}

interface MultiSelectProps {
    options: Option[];
    selected: (number | string)[];
    onChange: (selected: (number | string)[]) => void;
    placeholder?: string;
    className?: string;
}

export default function MultiSelect({
    options,
    selected,
    onChange,
    placeholder = 'Select items...',
    className = '',
}: MultiSelectProps) {
    const [isOpen, setIsOpen] = React.useState(false);
    const containerRef = React.useRef<HTMLDivElement>(null);

    // Close when clicking outside
    React.useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleSelect = (value: number | string) => {
        if (selected.includes(value)) {
            onChange(selected.filter((item) => item !== value));
        } else {
            onChange([...selected, value]);
        }
    };

    const removeSelect = (e: React.MouseEvent, value: number | string) => {
        e.stopPropagation();
        onChange(selected.filter((item) => item !== value));
    };

    const selectedOptions = options.filter((opt) => selected.includes(opt.value));

    return (
        <div className={`relative ${className}`} ref={containerRef}>
            <div
                className="flex min-h-[42px] w-full cursor-pointer flex-wrap items-center gap-2 rounded-lg border border-control-border bg-control-bg px-3 py-2 text-sm text-foreground hover:border-action focus-within:outline-2 focus-within:outline-offset-0 focus-within:outline-focus-ring"
                onClick={() => setIsOpen(!isOpen)}
            >
                {selectedOptions.length > 0 ? (
                    selectedOptions.map((option) => (
                        <span
                            key={option.value}
                            // A tag's colour is user data rather than a theme value, so it stays
                            // inline. The label is white because these are saturated mid-tones;
                            // an unrestricted picker cannot guarantee a ratio either way.
                            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-white"
                            style={{ backgroundColor: option.color || 'var(--action)' }}
                        >
                            {option.label}
                            <button
                                type="button"
                                aria-label={`Remove ${option.label}`}
                                className="ml-0.5 rounded-full p-0.5 hover:bg-white/20"
                                onClick={(e) => removeSelect(e, option.value)}
                            >
                                <X className="h-3.5 w-3.5" />
                            </button>
                        </span>
                    ))
                ) : (
                    <span className="text-control-placeholder">{placeholder}</span>
                )}
                <div className="ml-auto flex items-center">
                    <ChevronDown className={`h-4 w-4 text-contrast-icon-muted transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                </div>
            </div>

            {isOpen && (
                <div className="absolute z-[var(--z-dropdown)] mt-1 max-h-60 w-full overflow-auto rounded-lg border border-surface-float-border bg-surface-float p-1 shadow-float">
                    {options.length === 0 ? (
                        <div className="p-2 text-center text-sm text-contrast-helper">No options available</div>
                    ) : (
                        options.map((option) => {
                            const isSelected = selected.includes(option.value);
                            return (
                                <div
                                    key={option.value}
                                    className={`relative flex cursor-pointer select-none items-center rounded-lg px-2 py-2 text-sm outline-none transition-colors ${isSelected
                                            ? 'bg-action-tint text-action-tint-fg'
                                            : 'text-foreground hover:bg-surface-inset'
                                        }`}
                                    onClick={() => handleSelect(option.value)}
                                >
                                    <div className="mr-2 flex h-4 w-4 items-center justify-center rounded border border-control-border">
                                        {isSelected && <Check className="h-3 w-3 text-action-text" />}
                                    </div>
                                    <span className="flex-1 truncate">{option.label}</span>
                                    {option.color && (
                                        <span className="h-2 w-2 rounded-full ml-auto" style={{ backgroundColor: option.color }} />
                                    )}
                                </div>
                            );
                        })
                    )}
                </div>
            )}
        </div>
    );
}
