'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
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
  ChevronDown,
  Plus,
  X,
  Bell,
  LogOut,
  Download,
  Link2,
  RefreshCw,
  Edit2,
  ChevronsDown,
  ChevronsUp
} from 'lucide-react';
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  useDraggable,
  useDroppable,
  PointerSensor,
  useSensor,
  useSensors,
  pointerWithin
} from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { useNavigationStore, ViewType } from '@/lib/store';
import { useServiceStatusStore } from '@/lib/serviceStatusStore';
import { getTags, updateTag, deleteTag, createTag, getCompanionReleases, CompanionReleases } from '@/lib/api';
import { Tag } from '@/types';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';
import { InlineColorPicker } from './ColorPicker';

import ImportAudioModal from './ImportAudioModal';
import ConfirmationModal from './ConfirmationModal';
import CreateTagModal from './CreateTagModal';
import NotificationHistoryModal from './NotificationHistoryModal';
import { getDownloadUrl, detectPlatform } from '@/lib/platform';
import ContextMenu from './ContextMenu';

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
      <span className="shrink-0">{icon}</span>
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
  onAddChild: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  isEditing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  collapsed: boolean;
  level?: number;
  hasChildren: boolean;
  isExpanded: boolean;
  onToggleExpand: () => void;
}



function TagItem({
  tag,
  isSelected,
  onToggle,
  onColorChange,
  onDelete,
  onRename,
  onAddChild,
  onContextMenu,
  isEditing,
  onStartEdit,
  onCancelEdit,
  collapsed,
  level = 0,
  hasChildren,
  isExpanded,
  onToggleExpand
}: TagItemProps) {
  const color = getColorByKey(tag.color);
  const [editValue, setEditValue] = useState(tag.name);

  const { attributes, listeners, setNodeRef: setDraggableRef, transform, isDragging } = useDraggable({
    id: `tag-${tag.id}`,
    data: { tag }
  });

  const { setNodeRef: setDroppableRef, isOver } = useDroppable({
    id: `tag-${tag.id}`,
    data: { tag },
    disabled: isDragging
  });

  const style = {
    transform: CSS.Translate.toString(transform),
  };

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

  const content = (
    <div
      {...attributes}
      {...listeners}
      className={`
        group flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all cursor-pointer relative select-none touch-none
        ${isSelected ? 'bg-gray-100 dark:bg-gray-800' : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'}
        ${isDragging ? 'opacity-50' : ''}
        ${isOver ? 'bg-orange-50 dark:bg-orange-900/10 ring-2 ring-orange-400 dark:ring-orange-600' : ''}
      `}
      style={{ paddingLeft: `${level * 12 + 12}px` }}
      onClick={() => {
        if (!isEditing) onToggle();
      }}
      onContextMenu={onContextMenu}
    >
      <InlineColorPicker
        selectedColor={tag.color || undefined}
        onColorSelect={onColorChange}
      />
      {isEditing ? (
        <input
          type="text"
          value={editValue}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEditValue(e.target.value)}
          onKeyDown={(e: React.KeyboardEvent) => {
            if (e.key === 'Enter') handleSubmit();
            if (e.key === 'Escape') onCancelEdit();
          }}
          onBlur={handleSubmit}
          autoFocus
          className="flex-1 text-sm bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-1 py-0.5 focus:ring-1 focus:ring-orange-500 focus:outline-none select-text"
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        />
      ) : (
        <span
          className={`flex-1 text-sm truncate ${isSelected ? 'font-medium' : ''}`}
          title={tag.name}
          onDoubleClick={(e: React.MouseEvent) => {
            e.stopPropagation();
            onStartEdit();
          }}
        >
          {tag.name}
        </span>
      )}
      {!isEditing && (
        <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e: React.MouseEvent) => {
              e.stopPropagation();
              onAddChild();
            }}
            className="p-1 hover:text-orange-500 transition-all"
            title="Add sub-tag"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <Plus className="w-3 h-3" />
          </button>

          {hasChildren && (
            <button
              onClick={(e: React.MouseEvent) => {
                e.stopPropagation();
                onToggleExpand();
              }}
              className="p-1 hover:text-gray-600 dark:hover:text-gray-300 transition-all"
              title={isExpanded ? "Collapse" : "Expand"}
              onPointerDown={(e) => e.stopPropagation()}
            >
              {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            </button>
          )}

          <button
            onClick={(e: React.MouseEvent) => {
              e.stopPropagation();
              onDelete();
            }}
            className="p-1 hover:text-red-500 transition-all"
            title="Delete tag"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div ref={setDroppableRef} style={style}>
      <div ref={setDraggableRef}>
        {content}
      </div>
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
    toggleNavCollapse,
    navWidth,
    setNavWidth,
    expandedTagIds: expandedTagIdsArray,
    toggleExpandedTag,
    setExpandedTagIds
  } = useNavigationStore();
  const [isAuthorizing, setIsAuthorizing] = useState(false);
  const [companionReleases, setCompanionReleases] = useState<CompanionReleases | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const MIN_WIDTH = 224;
  const MAX_WIDTH = 400;
  const COLLAPSED_WIDTH = 64;

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
  const [createTagModal, setCreateTagModal] = useState<{
    isOpen: boolean;
    parentId?: number;
  }>({ isOpen: false });

  // Convert array to Set for easier lookup
  const expandedTagIds = useMemo(() => new Set(expandedTagIdsArray), [expandedTagIdsArray]);

  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    tagId: number;
  } | null>(null);

  // Close context menu on click outside
  useEffect(() => {
    const handleClick = () => setContextMenu(null);
    window.addEventListener('click', handleClick);
    return () => window.removeEventListener('click', handleClick);
  }, []);

  const handleUpdateCompanion = async () => {
    await triggerCompanionUpdate();
  };
  const [newTagName, setNewTagName] = useState('');
  const [editingTagId, setEditingTagId] = useState<number | null>(null);
  const [mounted, setMounted] = useState(false);

  // Modal states
  // const [isSpeakersModalOpen, setIsSpeakersModalOpen] = useState(false);
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
    onConfirm: () => { },
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

  // Handle resize
  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = e.clientX;
      if (newWidth >= MIN_WIDTH && newWidth <= MAX_WIDTH) {
        setNavWidth(newWidth);
      }
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing, setNavWidth]);

  // Build tag tree
  interface TagWithChildren extends Tag {
    children: TagWithChildren[];
  }

  const tagTree = useMemo(() => {
    const tagMap = new Map<number, TagWithChildren>();
    const roots: TagWithChildren[] = [];

    // First pass: create nodes
    tags.forEach((tag: Tag) => {
      tagMap.set(tag.id, { ...tag, children: [] });
    });

    // Second pass: build tree
    tags.forEach((tag: Tag) => {
      const node = tagMap.get(tag.id)!;
      if (tag.parent_id && tagMap.has(tag.parent_id)) {
        tagMap.get(tag.parent_id)!.children.push(node);
      } else {
        roots.push(node);
      }
    });

    return roots;
  }, [tags]);

  const handleColorChange = async (tagId: number, colorKey: string) => {
    try {
      await updateTag(tagId, { color: colorKey });
      setTags((prev: Tag[]) => prev.map((t: Tag) => t.id === tagId ? { ...t, color: colorKey } : t));
      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to update tag color:', error);
    }
  };

  const handleRenameTag = async (tagId: number, newName: string) => {
    try {
      await updateTag(tagId, { name: newName });
      setTags((prev: Tag[]) => prev.map((t: Tag) => t.id === tagId ? { ...t, name: newName } : t));
      setEditingTagId(null);
      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to rename tag:', error);
    }
  };

  const handleDeleteTag = (tagId: number) => {
    const tag = tags.find((t: Tag) => t.id === tagId);
    if (!tag) return;

    setConfirmModal({
      isOpen: true,
      title: 'Delete Tag',
      message: `Are you sure you want to delete the tag "${tag.name}"? This will remove it from all recordings.`,
      isDangerous: true,
      onConfirm: async () => {
        try {
          await deleteTag(tag.id);
          setTags((prev: Tag[]) => prev.filter((t: Tag) => t.id !== tag.id));
          window.dispatchEvent(new CustomEvent('tags-updated'));
        } catch (error) {
          console.error('Failed to delete tag:', error);
        }
      },
    });
  };

  const handleAddTag = async (parentId?: number) => {
    if (!newTagName.trim()) return;

    try {
      const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
      const newTag = await createTag(newTagName.trim(), randomColor, parentId);
      setTags((prev: Tag[]) => [...prev, newTag]);
      setNewTagName('');
      setIsAddingTag(false);
      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to create tag:', error);
    }
  };

  const handleAddSubTag = (parentId: number) => {
    setCreateTagModal({ isOpen: true, parentId });
  };

  const handleCreateTagConfirm = async (name: string) => {
    try {
      const parentId = createTagModal.parentId;
      const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
      const newTag = await createTag(name, randomColor, parentId);
      setTags((prev: Tag[]) => [...prev, newTag]);

      // If adding a sub-tag, ensure parent is expanded
      if (parentId && !expandedTagIds.has(parentId)) {
        toggleExpandedTag(parentId);
      }

      window.dispatchEvent(new CustomEvent('tags-updated'));
    } catch (error) {
      console.error('Failed to create tag:', error);
    }
  };

  const handleContextMenu = (e: React.MouseEvent, tagId: number) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      tagId
    });
  };

  const renderTagTree = (nodes: TagWithChildren[], level = 0) => {
    return nodes.map(node => (
      <React.Fragment key={node.id}>
        <TagItem
          tag={node}
          isSelected={selectedTagIds.includes(node.id)}
          onToggle={() => toggleTagFilter(node.id)}
          onColorChange={(color) => handleColorChange(node.id, color)}
          onDelete={() => handleDeleteTag(node.id)}
          onRename={(name) => handleRenameTag(node.id, name)}
          onAddChild={() => handleAddSubTag(node.id)}
          onContextMenu={(e) => handleContextMenu(e, node.id)}
          isEditing={editingTagId === node.id}
          onStartEdit={() => setEditingTagId(node.id)}
          onCancelEdit={() => setEditingTagId(null)}
          collapsed={isNavCollapsed}
          level={level}
          hasChildren={node.children.length > 0}
          isExpanded={expandedTagIds.has(node.id)}
          onToggleExpand={() => toggleExpandedTag(node.id)}
        />
        {node.children.length > 0 && expandedTagIds.has(node.id) && renderTagTree(node.children, level + 1)}
      </React.Fragment>
    ));
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

  // Drag and drop logic
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    })
  );

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;

    if (!over) return;

    // Extract tag IDs
    const activeIdString = String(active.id);
    const overIdString = String(over.id);

    const activeTagId = parseInt(activeIdString.replace('tag-', ''));
    let newParentId: number | null = null;

    if (overIdString === 'root-tags-drop-zone') {
      newParentId = null;
    } else if (overIdString.startsWith('tag-')) {
      const overTagId = parseInt(overIdString.replace('tag-', ''));
      // Don't do anything if dropped on itself
      if (activeTagId === overTagId) return;

      // Check for cycles: ensure overTagId is not a descendant of activeTagId
      const isDescendant = (parentId: number, childId: number): boolean => {
        if (parentId === childId) return true;
        const child = tags.find(t => t.id === childId);
        if (!child || !child.parent_id) return false;
        return isDescendant(parentId, child.parent_id);
      };

      if (isDescendant(activeTagId, overTagId)) {
        console.warn('Cannot move parent into child - cycle detected');
        return;
      }

      newParentId = overTagId;
    } else {
      return; // Dropped somewhere else
    }

    // Find the active tag object
    const activeTag = tags.find(t => t.id === activeTagId);
    if (!activeTag) return;

    // Don't update if parent hasn't changed
    if (activeTag.parent_id === newParentId) return;

    // Optimistic update - local state uses undefined for no parent
    setTags((prev) => prev.map(t =>
      t.id === activeTagId ? { ...t, parent_id: newParentId === null ? undefined : newParentId } : t
    ));

    try {
      // API Call - MUST send null to explicitly unset parent
      await updateTag(activeTagId, { parent_id: newParentId });
      window.dispatchEvent(new CustomEvent('tags-updated'));

      // If moved to a new parent, expand that parent
      if (newParentId && !expandedTagIds.has(newParentId)) {
        toggleExpandedTag(newParentId);
      }
    } catch (e) {
      console.error("Failed to move tag", e);
      // Revert optimistic update
      setTags((prev) => prev.map(t =>
        t.id === activeTagId ? { ...t, parent_id: activeTag.parent_id } : t
      ));
    }
  };

  const { isOver: isOverRoot, setNodeRef: setRootNodeRef } = useDroppable({
    id: 'root-tags-drop-zone',
  });

  return (
    <>
      <aside
        id="main-nav"
        className="shrink-0 border-r border-gray-300 dark:border-gray-800 bg-gray-100 dark:bg-gray-900 h-screen sticky top-0 flex flex-col"
        style={{
          width: collapsed ? `${COLLAPSED_WIDTH}px` : `${navWidth}px`,
          transition: isResizing ? 'none' : 'width 300ms'
        }}
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
        <DndContext
          sensors={sensors}
          collisionDetection={pointerWithin}
          onDragStart={() => { }}
          onDragEnd={handleDragEnd}
        >
          <div
            ref={setRootNodeRef}
            className={`flex-1 overflow-y-auto p-2 border-2 border-transparent rounded-lg transition-all ${isOverRoot ? 'border-orange-300 bg-orange-50/50 dark:border-orange-700 dark:bg-orange-900/10' : ''}`}
          >
            {!collapsed && (
              <div
                className="flex items-center justify-between px-3 py-2"
              >
                <span className="text-xs font-semibold uppercase text-gray-500 dark:text-gray-400 flex items-center gap-1">
                  <TagIcon className="w-3 h-3" />
                  Tags
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      const parentIds = tags
                        .filter(t => tags.some(child => child.parent_id === t.id))
                        .map(t => t.id);
                      setExpandedTagIds(parentIds);
                    }}
                    className="p-1 rounded hover:bg-gray-300 dark:hover:bg-gray-800 transition-colors text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                    title="Expand All"
                  >
                    <ChevronsDown className="w-3 h-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedTagIds([]);
                    }}
                    className="p-1 rounded hover:bg-gray-300 dark:hover:bg-gray-800 transition-colors text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                    title="Collapse All"
                  >
                    <ChevronsUp className="w-3 h-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsAddingTag(true);
                    }}
                    className="p-1 rounded hover:bg-gray-300 dark:hover:bg-gray-800 transition-colors text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                    title="Add tag"
                  >
                    <Plus className="w-3 h-3" />
                  </button>
                </div>
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
              {renderTagTree(tagTree)}
            </div>

            {tags.length === 0 && !collapsed && (
              <p className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                No tags yet. Create one to organize your recordings.
              </p>
            )}
          </div>
          <DragOverlay />
        </DndContext>

        {/* Context Menu */}
        {contextMenu && (
          <ContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={() => setContextMenu(null)}
            items={[
              {
                label: 'Rename',
                icon: <Edit2 className="w-4 h-4" />,
                onClick: () => {
                  setEditingTagId(contextMenu.tagId);
                  setContextMenu(null);
                }
              },
              {
                label: 'Add Sub-tag',
                icon: <Plus className="w-4 h-4" />,
                onClick: () => {
                  handleAddSubTag(contextMenu.tagId);
                  setContextMenu(null);
                }
              },
              ...(tags.find(t => t.id === contextMenu.tagId)?.parent_id ? [
                {
                  label: 'Promote to Root',
                  icon: <ChevronLeft className="w-4 h-4" />,
                  onClick: async () => {
                    try {
                      await updateTag(contextMenu.tagId, { parent_id: null }); // Send null to API
                      // Optimistic update (local state undefined)
                      setTags((prev) => prev.map(t =>
                        t.id === contextMenu.tagId ? { ...t, parent_id: undefined } : t
                      ));
                      window.dispatchEvent(new CustomEvent('tags-updated'));
                    } catch (e) { console.error(e); }
                    setContextMenu(null);
                  }
                },
                ...(tags.find(t => t.id === tags.find(curr => curr.id === contextMenu.tagId)?.parent_id)?.parent_id ? [
                  {
                    label: 'Promote One Level',
                    icon: <ChevronLeft className="w-4 h-4" />,
                    onClick: async () => {
                      const tag = tags.find(t => t.id === contextMenu.tagId);
                      if (tag?.parent_id) {
                        const parent = tags.find(t => t.id === tag.parent_id);
                        if (parent?.parent_id) {
                          try {
                            await updateTag(tag.id, { parent_id: parent.parent_id });
                            // Optimistic update
                            setTags((prev) => prev.map(t =>
                              t.id === tag.id ? { ...t, parent_id: parent.parent_id } : t
                            ));
                            window.dispatchEvent(new CustomEvent('tags-updated'));
                          } catch (e) { console.error(e); }
                        }
                      }
                      setContextMenu(null);
                    }
                  }
                ] : [])
              ] : []),
              {
                label: 'Delete',
                icon: <Trash2 className="w-4 h-4" />,
                className: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
                onClick: () => {
                  handleDeleteTag(contextMenu.tagId);
                  setContextMenu(null);
                }
              }
            ]}
          />
        )}

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
              <Download className="w-5 h-5 shrink-0" />
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
              <Link2 className={`w-5 h-5 shrink-0 ${isAuthorizing ? 'animate-pulse' : ''}`} />
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
              <RefreshCw className="w-5 h-5 shrink-0" />
              {!collapsed && (
                <span className="text-sm truncate">Update Companion App</span>
              )}
            </button>
          )}

          <NavItem
            id="nav-people"
            icon={<Users className="w-5 h-5" />}
            label="People"
            onClick={() => router.push('/people')}
            isActive={pathname.startsWith('/people')}
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

        {/* Resize Handle */}
        {!collapsed && (
          <div
            onMouseDown={() => setIsResizing(true)}
            className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-orange-500/50 active:bg-orange-500 transition-colors group"
            title="Drag to resize"
          >
            <div className="absolute top-1/2 right-0 -translate-y-1/2 w-1 h-12 bg-gray-400 dark:bg-gray-600 group-hover:bg-orange-500 transition-colors" />
          </div>
        )}
      </aside>

      {/* Modals */}
      <NotificationHistoryModal
        isOpen={isNotificationModalOpen}
        onClose={() => setIsNotificationModalOpen(false)}
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

      <CreateTagModal
        isOpen={createTagModal.isOpen}
        onClose={() => setCreateTagModal({ ...createTagModal, isOpen: false })}
        onConfirm={handleCreateTagConfirm}
        title={createTagModal.parentId ? "Add Sub-tag" : "Create Tag"}
        placeholder={createTagModal.parentId ? "Sub-tag name..." : "Tag name..."}
        confirmText="Create"
      />
    </>
  );
}
