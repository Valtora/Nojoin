// Extended color palette organized by hue for speakers, tags, and other features
// Each color has a unique key that can be stored in the database

export interface ColorOption {
  key: string;
  name: string;
  bg: string;
  border: string;
  text: string;
  dot: string;
}

export const COLOR_PALETTE: ColorOption[] = [
  // Reds
  { key: 'red', name: 'Red', bg: 'bg-red-100 dark:bg-red-900/30', border: 'border-red-300 dark:border-red-700', text: 'text-red-700 dark:text-red-400', dot: 'bg-red-500' },
  { key: 'rose', name: 'Rose', bg: 'bg-rose-100 dark:bg-rose-900/30', border: 'border-rose-300 dark:border-rose-700', text: 'text-rose-700 dark:text-rose-400', dot: 'bg-rose-500' },
  { key: 'pink', name: 'Pink', bg: 'bg-pink-100 dark:bg-pink-900/30', border: 'border-pink-300 dark:border-pink-700', text: 'text-pink-700 dark:text-pink-400', dot: 'bg-pink-500' },
  
  // Oranges
  { key: 'orange', name: 'Orange', bg: 'bg-orange-100 dark:bg-orange-900/30', border: 'border-orange-300 dark:border-orange-700', text: 'text-orange-700 dark:text-orange-400', dot: 'bg-orange-500' },
  { key: 'amber', name: 'Amber', bg: 'bg-amber-100 dark:bg-amber-900/30', border: 'border-amber-300 dark:border-amber-700', text: 'text-amber-700 dark:text-amber-400', dot: 'bg-amber-500' },
  
  // Yellows
  { key: 'yellow', name: 'Yellow', bg: 'bg-yellow-100 dark:bg-yellow-900/30', border: 'border-yellow-300 dark:border-yellow-700', text: 'text-yellow-700 dark:text-yellow-400', dot: 'bg-yellow-500' },
  { key: 'lime', name: 'Lime', bg: 'bg-lime-100 dark:bg-lime-900/30', border: 'border-lime-300 dark:border-lime-700', text: 'text-lime-700 dark:text-lime-400', dot: 'bg-lime-500' },
  
  // Greens
  { key: 'green', name: 'Green', bg: 'bg-green-100 dark:bg-green-900/30', border: 'border-green-300 dark:border-green-700', text: 'text-green-700 dark:text-green-400', dot: 'bg-green-500' },
  { key: 'emerald', name: 'Emerald', bg: 'bg-emerald-100 dark:bg-emerald-900/30', border: 'border-emerald-300 dark:border-emerald-700', text: 'text-emerald-700 dark:text-emerald-400', dot: 'bg-emerald-500' },
  { key: 'teal', name: 'Teal', bg: 'bg-teal-100 dark:bg-teal-900/30', border: 'border-teal-300 dark:border-teal-700', text: 'text-teal-700 dark:text-teal-400', dot: 'bg-teal-500' },
  
  // Cyans
  { key: 'cyan', name: 'Cyan', bg: 'bg-cyan-100 dark:bg-cyan-900/30', border: 'border-cyan-300 dark:border-cyan-700', text: 'text-cyan-700 dark:text-cyan-400', dot: 'bg-cyan-500' },
  { key: 'sky', name: 'Sky', bg: 'bg-sky-100 dark:bg-sky-900/30', border: 'border-sky-300 dark:border-sky-700', text: 'text-sky-700 dark:text-sky-400', dot: 'bg-sky-500' },
  
  // Blues
  { key: 'blue', name: 'Blue', bg: 'bg-blue-100 dark:bg-blue-900/30', border: 'border-blue-300 dark:border-blue-700', text: 'text-blue-700 dark:text-blue-400', dot: 'bg-blue-500' },
  { key: 'indigo', name: 'Indigo', bg: 'bg-indigo-100 dark:bg-indigo-900/30', border: 'border-indigo-300 dark:border-indigo-700', text: 'text-indigo-700 dark:text-indigo-400', dot: 'bg-indigo-500' },
  
  // Purples
  { key: 'violet', name: 'Violet', bg: 'bg-violet-100 dark:bg-violet-900/30', border: 'border-violet-300 dark:border-violet-700', text: 'text-violet-700 dark:text-violet-400', dot: 'bg-violet-500' },
  { key: 'purple', name: 'Purple', bg: 'bg-purple-100 dark:bg-purple-900/30', border: 'border-purple-300 dark:border-purple-700', text: 'text-purple-700 dark:text-purple-400', dot: 'bg-purple-500' },
  { key: 'fuchsia', name: 'Fuchsia', bg: 'bg-fuchsia-100 dark:bg-fuchsia-900/30', border: 'border-fuchsia-300 dark:border-fuchsia-700', text: 'text-fuchsia-700 dark:text-fuchsia-400', dot: 'bg-fuchsia-500' },
];

// Helper function to get a color option by key
export const getColorByKey = (key: string | null | undefined): ColorOption => {
  return COLOR_PALETTE.find(c => c.key === key) || COLOR_PALETTE[0];
};

// Legacy speaker colors for backward compatibility (maps to first 7 colors)
export const SPEAKER_COLORS = [
  'bg-purple-100 dark:bg-purple-900/30 border-purple-300 dark:border-purple-700',
  'bg-yellow-100 dark:bg-yellow-900/30 border-yellow-300 dark:border-yellow-700',
  'bg-orange-100 dark:bg-orange-900/30 border-orange-300 dark:border-orange-700',
  'bg-blue-100 dark:bg-blue-900/30 border-blue-300 dark:border-blue-700',
  'bg-green-100 dark:bg-green-900/30 border-green-300 dark:border-green-700',
  'bg-red-100 dark:bg-red-900/30 border-red-300 dark:border-red-700',
  'bg-gray-200 dark:bg-gray-800/30 border-gray-300 dark:border-gray-700',
];

// Default tag colors (subset of palette for initial tag creation)
export const DEFAULT_TAG_COLORS = ['blue', 'green', 'purple', 'orange', 'pink', 'teal', 'amber', 'indigo'];
