feat: enhance user management safety and UX

- **Backend**: Implemented check in `delete_user` to prevent deletion of the last remaining superuser.
- **Frontend**: Replaced native browser confirm dialog with `ConfirmationModal` in `AdminSettings` for safer user deletion.
- **Docs**: Updated PRD with new user management safeguards.
