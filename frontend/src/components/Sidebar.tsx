'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Recording, RecordingStatus, Tag } from '@/types';
import { Loader2, AlertCircle, HelpCircle, UploadCloud, Search, Filter, X, RotateCcw, Trash2, CheckSquare, Square } from 'lucide-react';
import MeetingControls from './MeetingControls';
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { 
  getRecordings, 
  renameRecording, 
  retryProcessing, 
  inferSpeakers,
  getGlobalSpeakers, 
  RecordingFilters,
  archiveRecording,
  restoreRecording,
  softDeleteRecording,
  permanentlyDeleteRecording,
  getTags
} from '@/lib/api';
import ContextMenu from './ContextMenu';
import ConfirmationModal from './ConfirmationModal';
import BatchActionBar from './BatchActionBar';
import { GlobalSpeaker } from '@/types';
import { useNavigationStore } from '@/lib/store';
import { useNotificationStore } from '@/lib/notificationStore';
import { createFuse } from '@/lib/searchUtils';
import { getColorByKey } from '@/lib/constants';

const formatDurationString = (seconds?: number) => {
  if (!seconds) return '0s';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  
  if (hours > 0) {
      return `${hours}hr ${minutes}${minutes === 1 ? 'min' : 'mins'}`;
  }
  return `${minutes}${minutes === 1 ? 'min' : 'mins'}`;
};

const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
};

const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
};

const StatusIcon = ({ status }: { status: RecordingStatus }) => {
  switch (status) {
    case RecordingStatus.PROCESSED:
      return null;
    case RecordingStatus.QUEUED:
      return (
        <span 
          className="cursor-help" 
          title="Meeting is in queue to be processed..."
        >
          <Loader2 className="w-3 h-3 text-blue-500 animate-spin" />
        </span>
      );
    case RecordingStatus.PROCESSING:
      return (
        <span 
          className="cursor-help" 
          title="Processing: transcription, diarization, voiceprints. Tip: Disable 'Auto-create Voiceprints' in Settings for faster processing."
        >
          <Loader2 className="w-3 h-3 text-blue-500 animate-spin" />
        </span>
      );
    case RecordingStatus.UPLOADING:
      return <UploadCloud className="w-3 h-3 text-orange-500 animate-pulse" />;
    case RecordingStatus.ERROR:
      return <AlertCircle className="w-3 h-3 text-red-500" />;
    default:
      return <HelpCircle className="w-3 h-3 text-gray-500" />;
  }
};

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { addNotification } = useNotificationStore();
  const prevRecordingsRef = useRef<Map<number, RecordingStatus>>(new Map());
  const prevNotesStatusRef = useRef<Map<number, string>>(new Map());
  const prevTranscriptStatusRef = useRef<Map<number, string>>(new Map());
  const { 
    currentView, 
    selectedTagIds, 
    toggleTagFilter,
    clearTagFilters,
    selectionMode,
    selectedRecordingIds,
    toggleRecordingSelection,
    selectAllRecordings,
    clearSelection
  } = useNavigationStore();
  
  const [mounted, setMounted] = useState(false);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; recording: Recording } | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  
  // Search & Filter State
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [globalSpeakers, setGlobalSpeakers] = useState<GlobalSpeaker[]>([]);
  const [selectedSpeakers, setSelectedSpeakers] = useState<number[]>([]);
  const [dateMode, setDateMode] = useState<'range' | 'before' | 'after'>('range');
  const [dateRange, setDateRange] = useState<{ start: string; end: string }>({ start: "", end: "" });
  const [tags, setTags] = useState<Tag[]>([]);

  // Load tags for filter pills
  useEffect(() => {
    const loadTags = async () => {
      try {
        const data = await getTags();
        setTags(data);
      } catch (error) {
        console.error('Failed to load tags:', error);
      }
    };
    void loadTags();
    
    const handleTagsUpdated = () => { void loadTags(); };
    window.addEventListener('tags-updated', handleTagsUpdated);
    return () => window.removeEventListener('tags-updated', handleTagsUpdated);
  }, []);

  useEffect(() => {
    recordings.forEach(rec => {
      // Check Recording Status (General Processing)
      const prevStatus = prevRecordingsRef.current.get(rec.id);
      if (prevStatus && prevStatus !== RecordingStatus.PROCESSED && rec.status === RecordingStatus.PROCESSED) {
         addNotification({
          type: 'success',
          message: `Processing completed for "${rec.name}"`,
        });
      }
      prevRecordingsRef.current.set(rec.id, rec.status);

      // Check Notes Status (Specific)
      if (rec.transcript) {
        const prevNotesStatus = prevNotesStatusRef.current.get(rec.id);
        const currentNotesStatus = rec.transcript.notes_status;
        
        if (prevNotesStatus && prevNotesStatus !== 'completed' && currentNotesStatus === 'completed') {
           addNotification({
            type: 'success',
            message: `Meeting notes generated for "${rec.name}"`,
          });
        }
        prevNotesStatusRef.current.set(rec.id, currentNotesStatus || 'pending');

        // Check Transcript Status (Specific)
        const prevTranscriptStatus = prevTranscriptStatusRef.current.get(rec.id);
        const currentTranscriptStatus = rec.transcript.transcript_status;
        
        if (prevTranscriptStatus && prevTranscriptStatus !== 'completed' && currentTranscriptStatus === 'completed') {
           addNotification({
            type: 'success',
            message: `Transcript ready for "${rec.name}"`,
          });
        }
        prevTranscriptStatusRef.current.set(rec.id, currentTranscriptStatus || 'pending');
      }
    });
  }, [recordings, addNotification]);

  const filteredRecordings = useMemo(() => {
    if (!debouncedSearchQuery) return recordings;
    
    const fuse = createFuse(recordings, [
      'name', 
      'status',
      'tags.name',
      'speakers.local_name',
      'speakers.global_speaker.name'
    ]);
    
    return fuse.search(debouncedSearchQuery).map(result => result.item);
  }, [recordings, debouncedSearchQuery]);

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Confirmation Modal State
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
    isDangerous?: boolean;
  }>({
    isOpen: false,
    title: "",
    message: "",
    onConfirm: () => {},
  });

  const fetchRecordings = useCallback(async () => {
    try {
      const filters: RecordingFilters = {};
      // Client-side search is now used, so we don't send 'q' to the backend
      // if (debouncedSearchQuery) filters.q = debouncedSearchQuery;
      
      if (dateMode === 'range') {
        if (dateRange.start) filters.start_date = new Date(dateRange.start).toISOString();
        if (dateRange.end) filters.end_date = new Date(dateRange.end).toISOString();
      } else if (dateMode === 'before') {
        if (dateRange.end) filters.end_date = new Date(dateRange.end).toISOString();
      } else if (dateMode === 'after') {
        if (dateRange.start) filters.start_date = new Date(dateRange.start).toISOString();
      }

      if (selectedSpeakers.length > 0) filters.speaker_ids = selectedSpeakers;
      if (selectedTagIds.length > 0) filters.tag_ids = selectedTagIds;

      // Apply view-based filters
      if (currentView === 'archived') {
        filters.only_archived = true;
      } else if (currentView === 'deleted') {
        filters.only_deleted = true;
      }

      const data = await getRecordings(filters);
      setRecordings(data);
    } catch (error) {
      console.error("Failed to fetch recordings:", error);
    }
  }, [dateRange, dateMode, selectedSpeakers, selectedTagIds, currentView]);

  useEffect(() => {
    fetchRecordings();
    const interval = setInterval(fetchRecordings, 15000);
    return () => clearInterval(interval);
  }, [fetchRecordings]);

  useEffect(() => {
    setMounted(true);
    getGlobalSpeakers().then(setGlobalSpeakers).catch(console.error);
  }, []);

  // Listen for recording-updated events
  useEffect(() => {
    const handleUpdate = () => fetchRecordings();
    window.addEventListener('recording-updated', handleUpdate);
    window.addEventListener('tags-updated', handleUpdate);
    return () => {
      window.removeEventListener('recording-updated', handleUpdate);
      window.removeEventListener('tags-updated', handleUpdate);
    };
  }, [fetchRecordings]);

  // Clear selection when view changes
  useEffect(() => {
    clearSelection();
  }, [currentView, clearSelection]);

  const handleContextMenu = (e: React.MouseEvent, recording: Recording) => {
    e.preventDefault();
    if (selectionMode) return; // Disable context menu in selection mode
    setContextMenu({ x: e.clientX, y: e.clientY, recording });
  };

  const handleArchive = async (id: number) => {
    setRecordings(prev => prev.filter(r => r.id !== id));
    try {
      await archiveRecording(id);
    } catch (e) {
      console.error("Failed to archive", e);
      fetchRecordings();
    }
  };

  const handleRestore = async (id: number) => {
    setRecordings(prev => prev.filter(r => r.id !== id));
    try {
      await restoreRecording(id);
    } catch (e) {
      console.error("Failed to restore", e);
      fetchRecordings();
    }
  };

  const handleSoftDelete = async (id: number) => {
    setRecordings(prev => prev.filter(r => r.id !== id));
    try {
      await softDeleteRecording(id);
      if (pathname === `/recordings/${id}`) {
        router.push('/');
      }
    } catch (e) {
      console.error("Failed to delete", e);
      fetchRecordings();
    }
  };

  const handlePermanentDelete = async (id: number) => {
    setConfirmModal({
      isOpen: true,
      title: "Permanently Delete Recording",
      message: "Are you sure you want to permanently delete this recording? This action cannot be undone.",
      isDangerous: true,
      onConfirm: async () => {
        setRecordings(prev => prev.filter(r => r.id !== id));
        try {
          await permanentlyDeleteRecording(id);
          if (pathname === `/recordings/${id}`) {
            router.push('/');
          }
        } catch (e) {
          console.error("Failed to permanently delete", e);
          fetchRecordings();
        }
      }
    });
  };

  const handleRenameStart = (id: number, currentName: string) => {
    setRenamingId(id);
    setRenameValue(currentName);
    setContextMenu(null);
  };

  const handleRenameSubmit = async (id: number) => {
    if (!renameValue.trim()) return;
    
    setRecordings(prev => prev.map(r => 
        r.id === id ? { ...r, name: renameValue } : r
    ));
    setRenamingId(null);

    // Dispatch event for optimistic update elsewhere
    window.dispatchEvent(new CustomEvent('recording-updated', { detail: { id, name: renameValue } }));

    try {
      await renameRecording(id, renameValue);
    } catch (e) {
      console.error("Failed to rename", e);
      fetchRecordings();
    }
  };

  const handleRetry = async (id: number) => {
    try {
      await retryProcessing(id);
      window.dispatchEvent(new CustomEvent('recording-updated', { detail: { id } }));
      fetchRecordings();
    } catch (e) {
      console.error("Failed to retry", e);
    }
  };

  const handleInferSpeakers = async (id: number) => {
    try {
      await inferSpeakers(id);
      addNotification({ message: "Speaker inference started. The speaker names will be updated shortly.", type: "success" });
      // Dispatch event to notify other components (like the detail page)
      window.dispatchEvent(new CustomEvent('recording-updated', { detail: { id } }));
      fetchRecordings();
    } catch (e) {
      console.error("Failed to infer speakers", e);
      addNotification({ message: "Failed to infer speakers.", type: "error" });
    }
  };

  const handleRecordingClick = (e: React.MouseEvent, recording: Recording, index: number) => {
    const isCtrl = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;

    if (isCtrl || isShift) {
        e.preventDefault();
        e.stopPropagation();
    }

    if (isCtrl) {
      toggleRecordingSelection(recording.id);
      setLastSelectedIndex(index);
      return;
    }

    if (isShift && lastSelectedIndex !== null) {
      const start = Math.min(lastSelectedIndex, index);
      const end = Math.max(lastSelectedIndex, index);
      const rangeIds = filteredRecordings.slice(start, end + 1).map(r => r.id);
      
      const newSelection = Array.from(new Set([...selectedRecordingIds, ...rangeIds]));
      selectAllRecordings(newSelection);
      return;
    }

    if (selectionMode) {
        clearSelection();
    }
    setLastSelectedIndex(index);
  };

  const getContextMenuItems = (recording: Recording) => {
    const items = [];

    if (view === 'recordings') {
      items.push(
        { 
          label: 'Rename', 
          onClick: () => handleRenameStart(recording.id, recording.name) 
        },
        {
          label: 'Retry Speaker Inference',
          onClick: () => handleInferSpeakers(recording.id)
        },
        {
          label: 'Retry Processing',
          onClick: () => handleRetry(recording.id)
        },
        { 
          label: 'Archive',
          onClick: () => handleArchive(recording.id)
        },
        { 
          label: 'Delete', 
          className: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
          onClick: () => handleSoftDelete(recording.id)
        },
      );
    } else if (view === 'archived') {
      items.push(
        { 
          label: 'Restore',
          icon: <RotateCcw className="w-4 h-4" />,
          onClick: () => handleRestore(recording.id)
        },
        { 
          label: 'Delete', 
          className: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
          onClick: () => handleSoftDelete(recording.id)
        },
      );
    } else if (view === 'deleted') {
      items.push(
        { 
          label: 'Restore',
          icon: <RotateCcw className="w-4 h-4" />,
          onClick: () => handleRestore(recording.id)
        },
        { 
          label: 'Delete Permanently', 
          icon: <Trash2 className="w-4 h-4" />,
          className: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
          onClick: () => handlePermanentDelete(recording.id)
        },
      );
    }

    return items;
  };

  const getEmptyMessage = () => {
    switch (view) {
      case 'archived': return { main: 'No archived recordings.', sub: 'Archived recordings will appear here.' };
      case 'deleted': return { main: 'No deleted recordings.', sub: 'Deleted recordings will appear here.' };
      default: return { main: 'No recordings found.', sub: 'Start a new meeting or import audio to get started.' };
    }
  };

  const hasActiveFilters = searchQuery || dateRange.start || dateRange.end || selectedSpeakers.length > 0 || selectedTagIds.length > 0;
  
  // Prevent hydration mismatch
  const view = mounted ? currentView : 'recordings';

  return (
    <aside 
      id="sidebar-recordings-list"
      className="w-80 flex-shrink-0 border-r border-gray-300 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 overflow-y-auto h-screen sticky top-0"
    >
      {view === 'recordings' && <MeetingControls onMeetingEnd={fetchRecordings} />}
      
      {/* Header */}
      <div className="p-4 border-b border-gray-300 dark:border-gray-800">
        {selectionMode ? (
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="text-xs text-gray-500">
              {selectedRecordingIds.length} selected
            </span>
            <button
              onClick={() => {
                if (selectedRecordingIds.length === filteredRecordings.length && filteredRecordings.length > 0) {
                  clearSelection();
                } else {
                  selectAllRecordings(filteredRecordings.map(r => r.id));
                }
              }}
              className="text-xs text-orange-600 hover:underline"
            >
              {selectedRecordingIds.length === filteredRecordings.length && filteredRecordings.length > 0 ? 'Deselect All' : 'Select All'}
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-4 py-2 text-sm bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-orange-500 text-gray-900 dark:text-gray-100"
              />
              <button
                onClick={() => setShowFilters(!showFilters)}
                className={`absolute right-2 top-1/2 transform -translate-y-1/2 p-1 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 ${showFilters ? 'text-orange-500' : 'text-gray-400'}`}
              >
                <Filter className="w-4 h-4" />
              </button>
            </div>
            {/* ... filters ... */}
          </div>
        )}

        {/* Active Tag Filter Pills */}
        {selectedTagIds.length > 0 && !selectionMode && (
          <div className="flex flex-wrap items-center gap-2 mt-2">
            {selectedTagIds.map(tagId => {
              const tag = tags.find(t => t.id === tagId);
              if (!tag) return null;
              const color = getColorByKey(tag.color);
              return (
                <div
                  key={tagId}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 text-xs group hover:border-orange-300 dark:hover:border-orange-700 transition-colors"
                >
                  <span className={`w-2 h-2 rounded-full ${color.dot} flex-shrink-0`} />
                  <span className="text-gray-700 dark:text-gray-200 font-medium">{tag.name}</span>
                  <button
                    onClick={() => toggleTagFilter(tagId)}
                    className="ml-0.5 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                    title={`Remove ${tag.name} filter`}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              );
            })}
            <button
              onClick={clearTagFilters}
              className="px-2.5 py-1 rounded-full text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 border border-red-200 dark:border-red-800 transition-colors"
              title="Clear all tag filters"
            >
              Clear All
            </button>
          </div>
        )}

        {showFilters && !selectionMode && (
            <div className="p-3 bg-white dark:bg-gray-900/50 rounded-lg border border-gray-300 dark:border-gray-700 space-y-3 text-sm shadow-sm mt-2">
              <div className="space-y-1">
                <label className="text-xs font-medium text-gray-500">Date Filter</label>
                <div className="flex gap-2 mb-2">
                    <button 
                        onClick={() => setDateMode('range')}
                        className={`text-xs px-2 py-1 rounded ${dateMode === 'range' ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' : 'bg-gray-200 dark:bg-gray-800'}`}
                    >
                        Range
                    </button>
                    <button 
                        onClick={() => setDateMode('after')}
                        className={`text-xs px-2 py-1 rounded ${dateMode === 'after' ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' : 'bg-gray-200 dark:bg-gray-800'}`}
                    >
                        After
                    </button>
                    <button 
                        onClick={() => setDateMode('before')}
                        className={`text-xs px-2 py-1 rounded ${dateMode === 'before' ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' : 'bg-gray-200 dark:bg-gray-800'}`}
                    >
                        Before
                    </button>
                </div>
                
                <div className="flex gap-2">
                  {(dateMode === 'range' || dateMode === 'after') && (
                  <input
                    type="date"
                    value={dateRange.start}
                    onChange={(e) => setDateRange(prev => ({ ...prev, start: e.target.value }))}
                    className="w-full px-2 py-1 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-xs"
                    placeholder="Start Date"
                  />
                  )}
                  {(dateMode === 'range' || dateMode === 'before') && (
                  <input
                    type="date"
                    value={dateRange.end}
                    onChange={(e) => setDateRange(prev => ({ ...prev, end: e.target.value }))}
                    className="w-full px-2 py-1 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded text-xs"
                    placeholder="End Date"
                  />
                  )}
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-gray-500">Speakers</label>
                <div className="flex flex-wrap gap-1">
                  {globalSpeakers.map(speaker => (
                    <button
                      key={speaker.id}
                      onClick={() => {
                        setSelectedSpeakers(prev => 
                          prev.includes(speaker.id) 
                            ? prev.filter(id => id !== speaker.id)
                            : [...prev, speaker.id]
                        );
                      }}
                      className={`px-2 py-1 rounded-full text-xs border ${
                        selectedSpeakers.includes(speaker.id)
                          ? 'bg-orange-100 border-orange-200 text-orange-700 dark:bg-orange-900/30 dark:border-orange-800 dark:text-orange-400'
                          : 'bg-white border-gray-200 text-gray-600 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-400'
                      }`}
                    >
                      {speaker.name}
                    </button>
                  ))}
                  {globalSpeakers.length === 0 && (
                    <span className="text-xs text-gray-400">No speakers yet</span>
                  )}
                </div>
              </div>
              
              {hasActiveFilters && (
                 <button 
                    onClick={() => {
                        setSearchQuery("");
                        setDateRange({ start: "", end: "" });
                        setSelectedSpeakers([]);
                        clearTagFilters();
                    }}
                    className="text-xs text-red-500 hover:underline flex items-center gap-1"
                 >
                    <X className="w-3 h-3" /> Clear Filters
                 </button>
              )}
            </div>
          )}
      </div>

      {/* Recording List */}
      <div className="p-2 space-y-2">
        {filteredRecordings.length === 0 && (
            <div className="text-center p-4 text-gray-500 dark:text-gray-400 text-sm">
                <p>{getEmptyMessage().main}</p>
                <p className="mt-1 text-xs">{getEmptyMessage().sub}</p>
            </div>
        )}
        {filteredRecordings.map((recording, index) => {
          const isActive = pathname === `/recordings/${recording.id}`;
          const startDate = new Date(recording.created_at);
          const endDate = new Date(startDate.getTime() + (recording.duration_seconds || 0) * 1000);
          const isRenaming = renamingId === recording.id;
          const isSelected = selectedRecordingIds.includes(recording.id);
          
          return (
            <div key={recording.id} className="relative group">
            {isRenaming ? (
                <div className="p-3 rounded-lg border bg-white dark:bg-gray-900 border-orange-500">
                    <input
                        autoFocus
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => handleRenameSubmit(recording.id)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') handleRenameSubmit(recording.id);
                            if (e.key === 'Escape') setRenamingId(null);
                        }}
                        className="w-full text-sm font-semibold bg-transparent focus:outline-none"
                    />
                </div>
            ) : (
            <div>
              <Link 
                href={`/recordings/${recording.id}`}
                id={recording.name === "Welcome to Nojoin" ? "demo-recording-card" : undefined}
                onClick={(e) => handleRecordingClick(e, recording, index)}
                onContextMenu={(e) => handleContextMenu(e, recording)}
                className={`block p-3 rounded-lg border transition-all shadow-sm ${
                  isActive && !selectionMode
                    ? 'bg-orange-100 dark:bg-orange-900/20 border-orange-500 dark:border-orange-500' 
                    : isSelected
                      ? 'bg-orange-100 dark:bg-orange-900/10 border-orange-400 dark:border-orange-600'
                      : 'bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-800 hover:border-orange-500 hover:bg-orange-100 dark:hover:border-orange-700 dark:hover:bg-gray-800'
                }`}
              >
                <div className="flex justify-between items-start mb-1">
                    <div className="flex items-center gap-2 min-w-0">
                      {selectionMode && (
                        <div className={`flex-shrink-0 ${isSelected ? 'text-orange-600' : 'text-gray-300'}`}>
                          {isSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
                        </div>
                      )}
                      <h3 
                        className={`text-sm font-semibold truncate ${isActive && !selectionMode ? 'text-orange-700 dark:text-orange-400' : 'text-gray-900 dark:text-gray-100'}`}
                        title="Double-click to rename"
                        onDoubleClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            handleRenameStart(recording.id, recording.name);
                        }}
                      >
                        {recording.name}
                      </h3>
                    </div>
                    <StatusIcon status={recording.status} />
                </div>
                
                <div className={`flex items-center text-xs text-gray-500 dark:text-gray-400 gap-2 ${selectionMode ? 'pl-6' : ''}`}>
                  <span>{formatDate(recording.created_at)}</span>
                  <span className="text-gray-300 dark:text-gray-700">|</span>
                  <span>{formatTime(startDate)} - {formatTime(endDate)}</span>
                  <span className="text-gray-300 dark:text-gray-700">|</span>
                  <span>{formatDurationString(recording.duration_seconds)}</span>
                </div>
              </Link>
            </div>
            )}
            </div>
          );
        })}
      </div>

      <BatchActionBar onActionComplete={fetchRecordings} />

      {contextMenu && (
        <ContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={() => setContextMenu(null)}
            items={getContextMenuItems(contextMenu.recording)}
        />
      )}
      
      <ConfirmationModal
        isOpen={confirmModal.isOpen}
        onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
        onConfirm={confirmModal.onConfirm}
        title={confirmModal.title}
        message={confirmModal.message}
        isDangerous={confirmModal.isDangerous}
      />
    </aside>
  );
}
