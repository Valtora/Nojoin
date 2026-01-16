# Git Commit Description

fix(ui): maintain sidebar highlight for active parent page

Updated the Main Sidebar navigation logic to ensure the current page (Recordings, Archived, or Deleted) remains highlighted when viewing a specific recording.
The highlighting now considers both the `currentView` state and whether the current path is a recording detail page (`/recordings/*`), preventing the active state from being lost during navigation.
