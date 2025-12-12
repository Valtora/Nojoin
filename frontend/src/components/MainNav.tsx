'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import Image from 'next/image';
import { 
  Mic, 
  Archive, 
  Trash2, 
  Tag as TagIcon, 
  Users, 
  FilePlus, 
  Settings, 
  ChevronLeft, 
  ChevronRight,
  Plus,
  X,
  Bell,
  LogOut,
  Download,
  Link2,
  RefreshCw
} from 'lucide-react';
import { useNavigationStore, ViewType } from '@/lib/store';
import { useServiceStatusStore } from '@/lib/serviceStatusStore';
import { getTags, updateTag, deleteTag, createTag, getCompanionReleases, CompanionReleases } from '@/lib/api';
import { Tag } from '@/types';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';
import { InlineColorPicker } from './ColorPicker';
import GlobalSpeakersModal from './GlobalSpeakersModal';
import ImportAudioModal from './ImportAudioModal';
import ConfirmationModal from './ConfirmationModal';
import NotificationHistoryModal from './NotificationHistoryModal';
import { getDownloadUrl, detectPlatform } from '@/lib/platform';

interface NavItemProps {
  icon: React.ReactNode;
  label: string;
  isActive?: boolean;
  onClick: () => void;
  collapsed: boolean;
  badge?: number;
  id?: string;
}

function NavItem({ icon, label, isActive, onClick, collapsed, badge, id }: NavItemProps) {
  return (
    <button
      id={id}
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`
        w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all
        ${isActive 
          ? 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400' 
          : 'text-gray-700 dark:text-gray-300 hover:bg-orange-200 hover:text-orange-800 dark:hover:bg-gray-800'
        }
        ${collapsed ? 'justify-center' : ''}
      `}
    >
      <span className="flex-shrink-0">{icon}</span>
      {!collapsed && (
        <>
          <span className="flex-1 text-left text-sm font-medium truncate">{label}</span>
          {badge !== undefined && badge > 0 && (
            <span className="text-xs bg-gray-200 dark:bg-gray-700 px-1.5 py-0.5 rounded-full">
              {badge}
            </span>
          )}
        </>
      )}
    </button>
  );
}

interface TagItemProps {
  tag: Tag;
  isSelected: boolean;
  onToggle: () => void;
  onColorChange: (colorKey: string) => void;
  onDelete: () => void;
  onRename: (newName: string) => void;
  isEditing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  collapsed: boolean;
}

function TagItem({ 
  tag, 
  isSelected, 
  onToggle, 
  onColorChange, 
  onDelete, 
  onRename,
  isEditing,
  onStartEdit,
  onCancelEdit,
  collapsed 
}: TagItemProps) {
  const color = getColorByKey(tag.color);
  const [editValue, setEditValue] = useState(tag.name);
  
  // Reset edit value when editing starts
  useEffect(() => {
    if (isEditing) {
      setEditValue(tag.name);
    }
  }, [isEditing, tag.name]);
  
  const handleSubmit = () => {
    if (editValue.trim() && editValue.trim() !== tag.name) {
      onRename(editValue.trim());
    } else {
      onCancelEdit();
    }
  };
  
  if (collapsed) {
    return (
      <button
        onClick={onToggle}
        title={tag.name}
        className={`
          w-full flex justify-center py-2 rounded-lg transition-all
          ${isSelected ? 'bg-gray-100 dark:bg-gray-800' : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'}
        `}
      >
        <span className={`w-3 h-3 rounded-full ${color.dot}`} />
      </button>
    );
  }

  return (
    <div 
      className={`
        group flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all cursor-pointer
        ${isSelected ? 'bg-gray-100 dark:bg-gray-800' : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'}
      `}
      onClick={isEditing ? undefined : onToggle}
    >
      <InlineColorPicker 
        selectedColor={tag.color || undefined} 
        onColorSelect={onColorChange} 
      />
      {isEditing ? (
        <input
          type="text"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSubmit();
            if (e.key === 'Escape') onCancelEdit();
          }}
          onBlur={handleSubmit}
          autoFocus
          className="flex-1 text-sm bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-1 py-0.5 focus:ring-1 focus:ring-orange-500 focus:outline-none"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span 
          className={`flex-1 text-sm truncate ${isSelected ? 'font-medium' : ''}`}
          title={tag.name}
          onDoubleClick={(e) => {
            e.stopPropagation();
            onStartEdit();
          }}
        >
          {tag.name}
        </span>
      )}
      {!isEditing && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-500 transition-all"
          title="Delete tag"
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

export default function MainNav() {
  const router = useRouter();
  const pathname = usePathname();
  const { companion, companionAuthenticated, authorizeCompanion, companionUpdateAvailable, triggerCompanionUpdate } = useServiceStatusStore();
  const { 
    currentView, 
    setCurrentView, 
    selectedTagIds, 
    toggleTagFilter,
    isNavCollapsed, 
    toggleNavCollapse 
  } = useNavigationStore();
  const [isAuthorizing, setIsAuthorizing] = useState(false);
  const [companionReleases, setCompanionReleases] = useState<CompanionReleases | null>(null);

  useEffect(() => {
    const fetchReleases = async () => {
      try {
        const releases = await getCompanionReleases();
        setCompanionReleases(releases);
      } catch (error) {
        console.error('Failed to fetch companion releases:', error);
      }
    };
    void fetchReleases();
  }, []);
  
  const [tags, setTags] = useState<Tag[]>([]);
  const [isAddingTag, setIsAddingTag] = useState(false);

  const handleUpdateCompanion = async () => {
    await triggerCompanionUpdate();
  };
  const [newTagName, setNewTagName] = useState('');
  const [editingTagId, setEditingTagId] = useState<number | null>(null);
  const [mounted, setMounted] = useState(false);
  
  // Modal states
  const [isSpeakersModalOpen, setIsSpeakersModalOpen] = useState(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [isNotificationModalOpen, setIsNotificationModalOpen] = useState(false);
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
    isDangerous?: boolean;
  }>({
    isOpen: false,
    title: '',
    message: '',
    onConfirm: () => {},
  });

  const handleLogout = () => {
    localStorage.removeItem('token');
    router.push('/login');
  };

  const loadTags = useCallback(async () => {
    try {
      const data = await getTags();
      setTags(data);
    } catch (error) {
      console.error('Failed to load tags:', error);
    }
  }, []);

  useEffect(() => {
    setMounted(true);
    void loadTags();
    
    // Listen for global tag updates
    const handleTagsUpdated = () => { void loadTags(); };
    window.addEventListener('tags-updated', handleTagsUpdated);
    return () => window.removeEventListener('tags-updated', handleTagsUpdated);
  }, [loadTags]);

  const handleColorChange = async (tagId: number, colorKey: string) => {
    try {
      await updateTag(tagId, { color: colorKey });
      setTags(prev => prev.map(t => t.id === tagId ? { ...t, color: colorKey } : t));
      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to update tag color:', error);
    }
  };

  const handleRenameTag = async (tagId: number, newName: string) => {
    try {
      await updateTag(tagId, { name: newName });
      setTags(prev => prev.map(t => t.id === tagId ? { ...t, name: newName } : t));
      setEditingTagId(null);
      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to rename tag:', error);
    }
  };

  const handleDeleteTag = (tag: Tag) => {
    setConfirmModal({
      isOpen: true,
      title: 'Delete Tag',
      message: `Are you sure you want to delete the tag "${tag.name}"? This will remove it from all recordings.`,
      isDangerous: true,
      onConfirm: async () => {
        try {
          await deleteTag(tag.id);
          setTags(prev => prev.filter(t => t.id !== tag.id));
          window.dispatchEvent(new CustomEvent('tags-updated'));
        } catch (error) {
          console.error('Failed to delete tag:', error);
        }
      },
    });
  };

  const handleAddTag = async () => {
    if (!newTagName.trim()) return;
    
    try {
      const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
      const newTag = await createTag(newTagName.trim(), randomColor);
      setTags(prev => [...prev, newTag]);
      setNewTagName('');
      setIsAddingTag(false);
      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to create tag:', error);
    }
  };

  const handleImportSuccess = () => {
    window.dispatchEvent(new CustomEvent('recording-updated'));
  };

  const handleDownloadCompanion = () => {
    const platform = detectPlatform();
    
    if (platform === 'windows' && companionReleases?.windows_url) {
      window.open(companionReleases.windows_url, '_blank');
      return;
    }

    const downloadUrl = getDownloadUrl();
    window.open(downloadUrl, '_blank');
  };

  const handleAuthorizeCompanion = async () => {
    setIsAuthorizing(true);
    try {
      await authorizeCompanion();
    } finally {
      setIsAuthorizing(false);
    }
  };

  // Prevent hydration mismatch by using default state until mounted
  const collapsed = mounted ? isNavCollapsed : false;
  
  // Determine which button to show
  const showDownloadButton = mounted && !companion;
  const showAuthorizeButton = mounted && companion && !companionAuthenticated;
  const showUpdateCompanionButton = mounted && companion && companionUpdateAvailable;

  const navItems: { view: ViewType; icon: React.ReactNode; label: string; id: string }[] = [
    { view: 'recordings', icon: <Mic className="w-5 h-5" />, label: 'Recordings', id: 'nav-recordings' },
    { view: 'archived', icon: <Archive className="w-5 h-5" />, label: 'Archived', id: 'nav-archived' },
    { view: 'deleted', icon: <Trash2 className="w-5 h-5" />, label: 'Deleted', id: 'nav-deleted' },
  ];

  return (
    <>
      <aside 
        id="main-nav"
        className={`
          flex-shrink-0 border-r border-gray-300 dark:border-gray-800 
          bg-gray-100 dark:bg-gray-900 h-screen sticky top-0 
          flex flex-col transition-all duration-300
          ${collapsed ? 'w-16' : 'w-56'}
        `}
      >
        {/* Header with collapse toggle */}
        <div className="p-3 flex items-center justify-between border-b border-gray-300 dark:border-gray-800">
          {!collapsed && (
            <div className="flex-1 text-center flex items-center justify-center gap-2">
              <Image 
                src="/assets/NojoinLogo.png" 
                alt="Nojoin Logo" 
                width={24} 
                height={24} 
                className="object-contain"
              />
              <span className="font-semibold text-orange-600">Nojoin</span>
            </div>
          )}
          <button
            onClick={toggleNavCollapse}
            className="p-1.5 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-800 transition-colors"
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            {collapsed ? (
              <ChevronRight className="w-4 h-4" />
            ) : (
              <ChevronLeft className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* Navigation Items */}
        <nav className="p-2 space-y-1">
          {navItems.map(({ view, icon, label, id }) => (
            <NavItem
              key={view}
              id={id}
              icon={icon}
              label={label}
              isActive={currentView === view && pathname === '/'}
              onClick={() => {
                setCurrentView(view);
                if (pathname !== '/') {
                  router.push('/');
                }
              }}
              collapsed={collapsed}
            />
          ))}
        </nav>

        {/* Divider */}
        <div className="mx-3 border-t border-gray-300 dark:border-gray-800" />

        {/* Tags Section */}
        <div className="flex-1 overflow-y-auto p-2">
          {!collapsed && (
            <div className="flex items-center justify-between px-3 py-2">
              <span className="text-xs font-semibold uppercase text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <TagIcon className="w-3 h-3" />
                Tags
              </span>
              <button
                onClick={() => setIsAddingTag(true)}
                className="p-1 rounded hover:bg-gray-300 dark:hover:bg-gray-800 transition-colors"
                title="Add tag"
              >
                <Plus className="w-3 h-3" />
              </button>
            </div>
          )}
          
          {collapsed && (
            <div className="flex justify-center py-2">
              <TagIcon className="w-4 h-4 text-gray-500" />
            </div>
          )}

          {/* Add Tag Input */}
          {isAddingTag && !collapsed && (
            <div className="px-2 pb-2">
              <input
                type="text"
                value={newTagName}
                onChange={(e) => setNewTagName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddTag();
                  if (e.key === 'Escape') {
                    setIsAddingTag(false);
                    setNewTagName('');
                  }
                }}
                onBlur={() => {
                  if (!newTagName.trim()) {
                    setIsAddingTag(false);
                  }
                }}
                placeholder="Tag name..."
                autoFocus
                className="w-full px-2 py-1 text-sm bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded focus:ring-2 focus:ring-orange-500 focus:outline-none"
              />
            </div>
          )}

          {/* Tag List */}
          <div className="space-y-0.5">
            {tags.map(tag => (
              <TagItem
                key={tag.id}
                tag={tag}
                isSelected={selectedTagIds.includes(tag.id)}
                onToggle={() => toggleTagFilter(tag.id)}
                onColorChange={(colorKey) => handleColorChange(tag.id, colorKey)}
                onDelete={() => handleDeleteTag(tag)}
                onRename={(newName) => handleRenameTag(tag.id, newName)}
                isEditing={editingTagId === tag.id}
                onStartEdit={() => setEditingTagId(tag.id)}
                onCancelEdit={() => setEditingTagId(null)}
                collapsed={collapsed}
              />
            ))}
          </div>

          {tags.length === 0 && !collapsed && (
            <p className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
              No tags yet. Create one to organize your recordings.
            </p>
          )}
        </div>

        {/* Divider */}
        <div className="mx-3 border-t border-gray-300 dark:border-gray-800" />

        {/* Action Buttons */}
        <div className="p-2 space-y-1">
          {/* Download Companion Button - Only shown when companion is not reachable */}
          {showDownloadButton && (
            <button
              id="nav-download-companion"
              onClick={handleDownloadCompanion}
              title={collapsed ? 'Download Companion' : undefined}
              className={`
                w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all
                bg-orange-600 hover:bg-orange-700 text-white font-medium
                ${collapsed ? 'justify-center' : ''}
              `}
            >
              <Download className="w-5 h-5 flex-shrink-0" />
              {!collapsed && (
                <span className="text-sm truncate">Download Companion</span>
              )}
            </button>
          )}

          {/* Authorize Companion Button - Only shown when companion is reachable but not authenticated */}
          {showAuthorizeButton && (
            <button
              id="nav-connect-companion"
              onClick={handleAuthorizeCompanion}
              disabled={isAuthorizing}
              title={collapsed ? 'Connect to Companion' : undefined}
              className={`
                w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all
                bg-orange-600 hover:bg-orange-700 text-white font-medium
                disabled:opacity-50 disabled:cursor-not-allowed
                ${collapsed ? 'justify-center' : ''}
              `}
            >
              <Link2 className={`w-5 h-5 flex-shrink-0 ${isAuthorizing ? 'animate-pulse' : ''}`} />
              {!collapsed && (
                <span className="text-sm truncate">
                  {isAuthorizing ? 'Connecting...' : 'Connect to Companion'}
                </span>
              )}
            </button>
          )}

          {/* Update Companion Button */}
          {showUpdateCompanionButton && (
            <button
              onClick={handleUpdateCompanion}
              title={collapsed ? 'Update Companion App' : undefined}
              className={`
                w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all
                bg-blue-600 hover:bg-blue-700 text-white font-medium
                ${collapsed ? 'justify-center' : ''}
              `}
            >
              <RefreshCw className="w-5 h-5 flex-shrink-0" />
              {!collapsed && (
                <span className="text-sm truncate">Update Companion App</span>
              )}
            </button>
          )}
          
          <NavItem
            id="nav-speakers"
            icon={<Users className="w-5 h-5" />}
            label="Speaker Library"
            onClick={() => setIsSpeakersModalOpen(true)}
            collapsed={collapsed}
          />
          <NavItem
            id="nav-import"
            icon={<FilePlus className="w-5 h-5" />}
            label="Import Audio"
            onClick={() => setIsImportModalOpen(true)}
            collapsed={collapsed}
          />
          <NavItem
            id="nav-notifications"
            icon={<Bell className="w-5 h-5" />}
            label="Notifications"
            onClick={() => setIsNotificationModalOpen(true)}
            collapsed={collapsed}
          />
          <NavItem
            id="nav-settings"
            icon={<Settings className="w-5 h-5" />}
            label="Settings"
            onClick={() => router.push('/settings')}
            collapsed={collapsed}
          />
          <NavItem
            id="nav-logout"
            icon={<LogOut className="w-5 h-5" />}
            label="Log Out"
            onClick={handleLogout}
            collapsed={collapsed}
          />
        </div>
      </aside>

      {/* Modals */}
      <NotificationHistoryModal
        isOpen={isNotificationModalOpen}
        onClose={() => setIsNotificationModalOpen(false)}
      />
      <GlobalSpeakersModal 
        isOpen={isSpeakersModalOpen} 
        onClose={() => setIsSpeakersModalOpen(false)} 
      />
      
      <ImportAudioModal
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
        onSuccess={handleImportSuccess}
      />

      <ConfirmationModal
        isOpen={confirmModal.isOpen}
        onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
        onConfirm={confirmModal.onConfirm}
        title={confirmModal.title}
        message={confirmModal.message}
        isDangerous={confirmModal.isDangerous}
      />
    </>
  );
}
