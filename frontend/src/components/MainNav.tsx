"use client";

import React, { useState, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Image from "next/image";
import {
  Mic,
  LayoutDashboard,
  ListTodo,
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
  ChevronsDown,
  ChevronsUp,
} from "lucide-react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  pointerWithin,
} from "@dnd-kit/core";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNavigationStore, ViewType } from "@/lib/store";
import { DESKTOP_BREAKPOINT } from "@/lib/viewportDensity";
import { logout } from "@/lib/api";

import ImportAudioModal from "./ImportAudioModal";
import ConfirmationModal from "./ConfirmationModal";
import CreateTagModal from "./CreateTagModal";
import NotificationHistoryModal from "./NotificationHistoryModal";
import ContextMenu from "./ContextMenu";
import { useViewportDensity } from "./ViewportDensityProvider";
import { useDragSelectionLock } from "@/lib/useDragSelectionLock";

import NavItem from "./mainNav/NavItem";
import TagItem from "./mainNav/TagItem";
import { TagWithChildren, useMainNavTags } from "./mainNav/useMainNavTags";

export default function MainNav() {
  const router = useRouter();
  const pathname = usePathname();
  const { pausedRecording, runtimeActive } = useCapture();
  const { isCompact } = useViewportDensity();
  const {
    currentView,
    setCurrentView,
    selectedTagIds,
    toggleTagFilter,
    isNavCollapsed,
    toggleNavCollapse,
    navWidth,
    setNavWidth,
    isMobileNavOpen,
    setMobileNavOpen,
  } = useNavigationStore();
  const [isResizing, setIsResizing] = useState(false);
  useDragSelectionLock(isResizing);
  const MIN_WIDTH = isCompact ? 208 : 224;
  const MAX_WIDTH = isCompact ? 360 : 400;
  const COLLAPSED_WIDTH = isCompact ? 60 : 64;
  const resolvedNavWidth = isCompact ? Math.min(navWidth, 248) : navWidth;

  const {
    tags,
    tagTree,
    expandedTagIds,
    toggleExpandedTag,
    setExpandedTagIds,
    isAddingTag,
    setIsAddingTag,
    newTagName,
    setNewTagName,
    editingTagId,
    setEditingTagId,
    createTagModal,
    setCreateTagModal,
    contextMenu,
    setContextMenu,
    confirmModal,
    setConfirmModal,
    isOverRoot,
    setRootNodeRef,
    handleColorChange,
    handleRenameTag,
    handleDeleteTag,
    handleAddTag,
    handleAddSubTag,
    handleCreateTagConfirm,
    handleContextMenu,
    handleDragEnd,
    handlePromoteToRoot,
    handlePromoteOneLevel,
    handleExpandAllTags,
  } = useMainNavTags();

  const [mounted, setMounted] = useState(false);
  const [isDesktop, setIsDesktop] = useState(false);

  // Modal states
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [isNotificationModalOpen, setIsNotificationModalOpen] = useState(false);
  const hasPausedCaptureLock = Boolean(pausedRecording && !runtimeActive);

  const handleLogout = async () => {
    await logout();
  };

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const syncViewport = () => {
      setIsDesktop(window.innerWidth >= DESKTOP_BREAKPOINT);
    };

    syncViewport();
    window.addEventListener("resize", syncViewport);

    return () => window.removeEventListener("resize", syncViewport);
  }, []);

  // Handle resize
  useEffect(() => {
    if (!isResizing) return;

    const handlePointerMove = (e: PointerEvent) => {
      // Limit width on desktop, don't resize on mobile
      if (window.innerWidth < DESKTOP_BREAKPOINT) return;
      const newWidth = e.clientX;
      if (newWidth >= MIN_WIDTH && newWidth <= MAX_WIDTH) {
        setNavWidth(newWidth);
      }
    };

    const handlePointerUp = () => {
      setIsResizing(false);
    };

    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp);
    document.addEventListener("pointercancel", handlePointerUp);

    return () => {
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
      document.removeEventListener("pointercancel", handlePointerUp);
    };
  }, [isResizing, MAX_WIDTH, MIN_WIDTH, setNavWidth]);

  const handleResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    window.getSelection()?.removeAllRanges();
    setIsResizing(true);
  };

  // Close mobile nav on route change
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname, setMobileNavOpen]);

  // `parentLines` carries the tree-guide flags from the ancestors down to these
  // nodes (see TagItem's TagGuides). Roots receive `null` and render no guides;
  // each child appends its own "has sibling below" flag so the parent's
  // connector becomes the child's ancestor rail.
  const renderTagTree = (
    nodes: TagWithChildren[],
    parentLines: boolean[] | null = null,
  ) => {
    return nodes.map((node, index) => {
      const hasSiblingBelow = index < nodes.length - 1;
      const lines = parentLines === null ? [] : [...parentLines, hasSiblingBelow];
      return (
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
            lines={lines}
            hasChildren={node.children.length > 0}
            childCount={node.children.length}
            isExpanded={expandedTagIds.has(node.id)}
            onToggleExpand={() => toggleExpandedTag(node.id)}
          />
          {/* Collapsed sidebar shows root tags only, so stop recursing. */}
          {!isNavCollapsed &&
            node.children.length > 0 &&
            expandedTagIds.has(node.id) &&
            renderTagTree(node.children, lines)}
        </React.Fragment>
      );
    });
  };

  const handleImportSuccess = () => {
    window.dispatchEvent(new CustomEvent("recording-updated"));
  };

  // Prevent hydration mismatch by using default state until mounted
  const collapsed = mounted ? (isDesktop ? isNavCollapsed : false) : false;
  const isDashboardRoute = pathname === "/";
  const isTasksRoute = pathname === "/tasks";
  const isRecordingsRoute =
    pathname === "/recordings" || pathname.startsWith("/recordings/");

  const navItems: {
    view: ViewType;
    icon: React.ReactNode;
    label: string;
    id: string;
  }[] = [
    {
      view: "recordings",
      icon: <Mic className="w-5 h-5" />,
      label: "Recordings",
      id: "nav-recordings",
    },
    {
      view: "archived",
      icon: <Archive className="w-5 h-5" />,
      label: "Archived",
      id: "nav-archived",
    },
    {
      view: "deleted",
      icon: <Trash2 className="w-5 h-5" />,
      label: "Deleted",
      id: "nav-deleted",
    },
  ];

  // Drag and drop logic
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
  );

  const contextMenuTag = contextMenu
    ? tags.find((t) => t.id === contextMenu.tagId)
    : undefined;
  const contextMenuParent = contextMenuTag
    ? tags.find((t) => t.id === contextMenuTag.parent_id)
    : undefined;

  return (
    <>
      {/* Mobile Overlay */}
      {isMobileNavOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-black/50 z-40 transition-opacity"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <aside
        id="main-nav"
        className={`shrink-0 border-r border-orange-100 dark:border-gray-800/80 bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.20),_transparent_45%),linear-gradient(180deg,_#fff7ed_0%,_#fffbf5_100%)] dark:bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.14),_transparent_45%),linear-gradient(180deg,_#0b1220_0%,_#0a0f1c_100%)] flex h-[calc(100dvh-1rem)] flex-col overflow-hidden z-50 transition-all duration-300 lg:sticky lg:top-0 lg:h-screen ${
          isMobileNavOpen
            ? "fixed inset-y-2 left-2 translate-x-0 rounded-[1.75rem] shadow-2xl lg:inset-auto lg:left-0 lg:rounded-none lg:shadow-none"
            : "fixed inset-y-2 left-2 -translate-x-[calc(100%+1rem)] rounded-[1.75rem] lg:relative lg:inset-auto lg:left-0 lg:translate-x-0 lg:rounded-none lg:shadow-none"
        }`}
        style={{
          width: isDesktop
            ? collapsed
              ? `${COLLAPSED_WIDTH}px`
              : `${resolvedNavWidth}px`
            : "min(22rem, calc(100vw - 1rem))",
        }}
      >
        {/* Header with collapse toggle */}
        <div className="p-3 flex items-center justify-between border-b border-orange-100/80 dark:border-gray-800/80">
          {!collapsed && (
            <div className="flex-1 text-center flex items-center justify-center gap-2">
              <Image
                src="/assets/NojoinLogo.png"
                alt="Nojoin Logo"
                width={48}
                height={48}
                className="object-contain shrink-0"
              />
              <span className="font-semibold text-orange-600 text-2xl">
                Nojoin
              </span>
            </div>
          )}
          <div className="flex items-center">
            {/* Close button for mobile */}
            <button
              onClick={() => setMobileNavOpen(false)}
              className="lg:hidden p-1.5 rounded-lg hover:bg-orange-200/70 dark:hover:bg-gray-800/80 transition-colors"
              title="Close Menu"
            >
              <X className="w-5 h-5 text-gray-500" />
            </button>
            {/* Desktop collapse toggle */}
            <button
              onClick={toggleNavCollapse}
              className="hidden lg:block p-1.5 rounded-lg hover:bg-orange-200/70 dark:hover:bg-gray-800/80 transition-colors"
              title={collapsed ? "Expand" : "Collapse"}
            >
              {collapsed ? (
                <ChevronRight className="w-4 h-4" />
              ) : (
                <ChevronLeft className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>

        {/* Navigation Items */}
        <nav className="p-2 space-y-1">
          <NavItem
            id="nav-dashboard"
            icon={<LayoutDashboard className="w-5 h-5" />}
            label="Dashboard"
            isActive={isDashboardRoute}
            onClick={() => {
              if (!isDashboardRoute) {
                router.push("/");
              }
            }}
            collapsed={collapsed}
          />

          <NavItem
            id="nav-tasks"
            icon={<ListTodo className="w-5 h-5" />}
            label="Tasks"
            isActive={isTasksRoute}
            onClick={() => {
              if (!isTasksRoute) {
                router.push("/tasks");
              }
            }}
            collapsed={collapsed}
          />

          <NavItem
            id="nav-people"
            icon={<Users className="w-5 h-5" />}
            label="People"
            onClick={() => router.push("/people")}
            isActive={pathname.startsWith("/people")}
            collapsed={collapsed}
          />

          {navItems.map(({ view, icon, label, id }) => (
            <NavItem
              key={view}
              id={id}
              icon={icon}
              label={label}
              isActive={currentView === view && isRecordingsRoute}
              onClick={() => {
                setCurrentView(view);
                if (pathname !== "/recordings") {
                  router.push("/recordings");
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
          onDragStart={() => {}}
          onDragEnd={handleDragEnd}
        >
          <div
            ref={setRootNodeRef}
            className={`flex-1 overflow-y-auto p-2 border-2 border-transparent rounded-lg transition-all ${isOverRoot ? "border-orange-300 bg-orange-50/50 dark:border-orange-700 dark:bg-orange-900/10" : ""}`}
          >
            {!collapsed && (
              <div className="flex items-center justify-between px-3 py-2">
                <span className="text-xs font-semibold uppercase text-gray-500 dark:text-gray-400 flex items-center gap-1">
                  <TagIcon className="w-3 h-3" />
                  Tags
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleExpandAllTags();
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
                    if (e.key === "Enter") handleAddTag();
                    if (e.key === "Escape") {
                      setIsAddingTag(false);
                      setNewTagName("");
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
            <div className="space-y-0.5">{renderTagTree(tagTree)}</div>

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
                label: "Rename",
                onClick: () => {
                  setEditingTagId(contextMenu.tagId);
                  setContextMenu(null);
                },
              },
              {
                label: "Add Sub-tag",
                onClick: () => {
                  handleAddSubTag(contextMenu.tagId);
                  setContextMenu(null);
                },
              },
              ...(contextMenuTag?.parent_id
                ? [
                    {
                      label: "Promote to Root",
                      onClick: () => {
                        void handlePromoteToRoot(contextMenu.tagId);
                      },
                    },
                    ...(contextMenuParent?.parent_id
                      ? [
                          {
                            label: "Promote One Level",
                            onClick: () => {
                              void handlePromoteOneLevel(contextMenu.tagId);
                            },
                          },
                        ]
                      : []),
                  ]
                : []),
              {
                label: "Delete",
                className:
                  "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20",
                onClick: () => {
                  handleDeleteTag(contextMenu.tagId);
                  setContextMenu(null);
                },
              },
            ]}
          />
        )}

        {/* Divider */}
        <div className="mx-3 border-t border-gray-300 dark:border-gray-800" />

        {/* Action Buttons */}
        <div className="p-2 space-y-1">
          <NavItem
            id="nav-import"
            icon={<FilePlus className="w-5 h-5" />}
            label="Import Audio"
            onClick={() => setIsImportModalOpen(true)}
            collapsed={collapsed}
            disabled={hasPausedCaptureLock}
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
            onClick={() => router.push("/settings")}
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

        {/* Resize Handle - Hidden on Mobile */}
        {!collapsed && (
          <div
            className="absolute right-0 top-0 bottom-0 hidden w-1 cursor-col-resize touch-none hover:bg-orange-500/50 active:bg-orange-500 lg:block"
            onPointerDown={handleResizePointerDown}
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
        placeholder={
          createTagModal.parentId ? "Sub-tag name..." : "Tag name..."
        }
        confirmText="Create"
      />
    </>
  );
}
