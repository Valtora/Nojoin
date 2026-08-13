'use client';

import { useState, useRef, useEffect } from 'react';
import { Check } from 'lucide-react';
import { COLOR_PALETTE, ColorOption } from '@/lib/constants';
import { useAnchoredPanel } from '@/components/ui/useAnchoredPanel';

interface ColorPickerProps {
  selectedColor?: string;
  onColorSelect: (colorKey: string) => void;
  trigger?: React.ReactNode;
  className?: string;
}

export default function ColorPicker({
  selectedColor,
  onColorSelect,
  trigger,
  className = ''
}: ColorPickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  // Anchored to the window rather than to the trigger's scroll box. PersonModal
  // puts this near the foot of a long form, where an absolutely positioned panel
  // renders into the part the modal clips and reads as not having opened.
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(isOpen, containerRef);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEscape);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  const handleColorClick = (color: ColorOption) => {
    onColorSelect(color.key);
    setIsOpen(false);
  };

  const selectedColorOption = COLOR_PALETTE.find(c => c.key === selectedColor);

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      {trigger ? (
        <div onClick={() => setIsOpen(!isOpen)}>
          {trigger}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-center gap-2 px-3 py-2 bg-surface-card border border-control-border rounded-lg hover:bg-surface-inset transition-colors"
        >
          <span className={`w-4 h-4 rounded-full ${selectedColorOption?.dot || 'bg-surface-card'}`} />
          <span className="text-sm text-contrast-muted">
            {selectedColorOption?.name || 'Select color'}
          </span>
        </button>
      )}

      {isOpen && (
        <div
          ref={panelRef}
          style={panelStyle}
          className="z-[var(--z-dropdown)] overflow-y-auto p-3 bg-surface-card rounded-xl shadow-float border border-surface-border min-w-[280px]"
        >
          <div className="grid grid-cols-6 gap-2">
            {COLOR_PALETTE.map((color) => (
              <button
                key={color.key}
                type="button"
                onClick={() => handleColorClick(color)}
                title={color.name}
                className={`
                  w-8 h-8 rounded-lg flex items-center justify-center transition-all
                  ${color.dot}
                  hover:scale-110 hover:shadow-card
                  focus:outline-none focus:ring-2 focus:ring-offset-2 focus-visible:outline-focus-ring
                  ${selectedColor === color.key ? 'ring-2 ring-offset-2 ring-control-border' : ''}
                `}
              >
                {selectedColor === color.key && (
                  <Check className="w-4 h-4 text-foreground drop-shadow-md" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Compact inline color picker for use in lists
interface InlineColorPickerProps {
  selectedColor?: string;
  onColorSelect: (colorKey: string) => void;
}

export function InlineColorPicker({ selectedColor, onColorSelect }: InlineColorPickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  // Same reason as above, a different enclosure: this one opens from rows in the
  // tag rail and the speaker panel, both of which scroll and clip.
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(isOpen, containerRef);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const selectedColorOption = COLOR_PALETTE.find(c => c.key === selectedColor);

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(!isOpen);
        }}
        className={`w-3 h-3 rounded-full ${selectedColorOption?.dot || 'bg-surface-card'} hover:ring-2 hover:ring-offset-1 hover:ring-control-border transition-all`}
        title="Change color"
      />

      {isOpen && (
        <div
          ref={panelRef}
          style={panelStyle}
          className="z-[var(--z-dropdown)] overflow-y-auto p-2 bg-surface-card rounded-lg shadow-float border border-surface-border min-w-max"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="grid grid-cols-6 gap-2">
            {COLOR_PALETTE.map((color) => (
              <button
                key={color.key}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onColorSelect(color.key);
                  setIsOpen(false);
                }}
                title={color.name}
                className={`
                  w-5 h-5 rounded flex items-center justify-center transition-all
                  ${color.dot}
                  hover:brightness-110
                  ${selectedColor === color.key ? 'ring-1 ring-offset-1 ring-control-border' : ''}
                `}
              >
                {selectedColor === color.key && (
                  <Check className="w-3 h-3 text-foreground drop-shadow-md" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
