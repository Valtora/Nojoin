'use client';

import { useState, useRef, useEffect } from 'react';
import { Check } from 'lucide-react';
import { COLOR_PALETTE, ColorOption } from '@/lib/constants';

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
          className="flex items-center gap-2 px-3 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
        >
          <span className={`w-4 h-4 rounded-full ${selectedColorOption?.dot || 'bg-gray-400'}`} />
          <span className="text-sm text-gray-700 dark:text-gray-300">
            {selectedColorOption?.name || 'Select color'}
          </span>
        </button>
      )}

      {isOpen && (
        <div className="absolute z-50 mt-2 p-3 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700 min-w-[280px]">
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
                  hover:scale-110 hover:shadow-md
                  focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500
                  ${selectedColor === color.key ? 'ring-2 ring-offset-2 ring-gray-900 dark:ring-white' : ''}
                `}
              >
                {selectedColor === color.key && (
                  <Check className="w-4 h-4 text-white drop-shadow-md" />
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
        className={`w-3 h-3 rounded-full ${selectedColorOption?.dot || 'bg-gray-400'} hover:ring-2 hover:ring-offset-1 hover:ring-gray-400 transition-all`}
        title="Change color"
      />
      
      {isOpen && (
        <div 
          className="absolute left-0 top-full mt-1 z-50 p-2 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 min-w-max"
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
                  ${selectedColor === color.key ? 'ring-1 ring-offset-1 ring-gray-900 dark:ring-white' : ''}
                `}
              >
                {selectedColor === color.key && (
                  <Check className="w-3 h-3 text-white drop-shadow-md" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
