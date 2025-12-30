'use client';

import React, { useState } from 'react';
import { useSearchParams } from 'next/navigation';

interface PeopleFiltersProps {
  onSearch: (query: string) => void;
}

export function PeopleFilters({ onSearch }: PeopleFiltersProps) {
  const searchParams = useSearchParams();
  const [search, setSearch] = useState(searchParams.get('q') || '');

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setSearch(value);
    onSearch(value);
  };

  return (
    <div className="flex flex-col sm:flex-row gap-4 mb-6">
      <div className="flex-1">
        <input
          type="text"
          placeholder="Search people by name, email, company..."
          value={search}
          onChange={handleSearch}
          className="w-full px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none transition-colors"
        />
      </div>
    </div>
  );
}
