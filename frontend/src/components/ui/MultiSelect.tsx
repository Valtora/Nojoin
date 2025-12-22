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
                className="flex min-h-[42px] w-full flex-wrap items-center gap-2 rounded-xl border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 text-sm ring-offset-background cursor-pointer hover:border-orange-500 focus-within:ring-2 focus-within:ring-orange-500 focus-within:ring-offset-2"
                onClick={() => setIsOpen(!isOpen)}
            >
                {selectedOptions.length > 0 ? (
                    selectedOptions.map((option) => (
                        <span
                            key={option.value}
                            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-white"
                            style={{ backgroundColor: option.color || '#f97316' }} // Default orange-500
                        >
                            {option.label}
                            <button
                                type="button"
                                className="ml-1 rounded-full hover:bg-white/20"
                                onClick={(e) => removeSelect(e, option.value)}
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </span>
                    ))
                ) : (
                    <span className="text-gray-500 dark:text-gray-400">{placeholder}</span>
                )}
                <div className="ml-auto flex items-center">
                    <ChevronDown className={`h-4 w-4 text-gray-500 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                </div>
            </div>

            {isOpen && (
                <div className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-1 shadow-lg animate-in fade-in zoom-in-95">
                    {options.length === 0 ? (
                        <div className="p-2 text-center text-sm text-gray-500">No options available</div>
                    ) : (
                        options.map((option) => {
                            const isSelected = selected.includes(option.value);
                            return (
                                <div
                                    key={option.value}
                                    className={`relative flex cursor-pointer select-none items-center rounded-lg px-2 py-2 text-sm outline-none transition-colors ${isSelected
                                            ? 'bg-orange-50 dark:bg-orange-900/20 text-orange-900 dark:text-orange-100'
                                            : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100'
                                        }`}
                                    onClick={() => handleSelect(option.value)}
                                >
                                    <div className="mr-2 flex h-4 w-4 items-center justify-center rounded border border-gray-300 dark:border-gray-600">
                                        {isSelected && <Check className="h-3 w-3 text-orange-600 dark:text-orange-400" />}
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
