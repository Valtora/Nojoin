# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.0.8.2]

### Documentation
- **Docs**: Renamed `SETUP.md` to `Nojoin-Development-Instructions.md`.
- **Docs**: Overhauled development guide with comprehensive setup, workflow, and troubleshooting instructions.
- **Docs**: Established "Release on Push" changelog workflow.

### Added
- **UI**: Global notification system using `zustand` store (`notificationStore`).
- **UI**: `NotificationToast` component for real-time, auto-dismissing alerts.
- **UI**: `NotificationHistoryModal` to view a log of past system notifications.
- **UI**: "Notifications" button in the Main Navigation bar with an unread badge count.
- **UI**: Success/Error notifications when saving changes in the Settings page.

### Changed
- **UI**: Refactored `ServiceStatusAlerts` to use the global notification system instead of local state. Service outages now persist in history.
- **UI**: Centered the "Nojoin" text in the Main Navigation bar and updated its color to Orange to match the branding.
- **Dependencies**: Added `date-fns` package for timestamp formatting.
