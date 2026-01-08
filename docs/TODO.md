# Nojoin To-Do List

## Admin Panel - System

- Add a "Restart" button to the admin panel -> system page in Settings. This button should gracefully shutdown then restart the nojoin instance which includes all the containers, 'nojoin-api' 'nojoin-db' 'nojoin-redis' 'nojoin-frontend' 'nojoin-nginx' 'nojoin-worker'.
- Remove the 'Infrastructure' section from the admin panel -> system page in Settings.
- Add a "Logs" section to the admin panel -> system page in Settings that shows the live logs of the nojoin instance. Note that there are several containers running in the nojoin instance, so the logs should be filtered by container name. The logs should all be shown in one running log with the option to filter by container name. There should also be the option to dump the logs in text format so the user can download it from the browser.

## Backup/Restore System

- Confirm the backup functionality correctly captures People and associated PeopleTags.
- Use the newly implemented 'Restart' button to restart the nojoin instance after a restoration is completed.
