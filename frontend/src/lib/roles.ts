import { UserRole, type User } from "@/types";

/*
 * The role checks the API makes, mirrored so the UI offers only what the API
 * would accept. The API stays the boundary; these decide what is shown.
 */

/**
 * An Owner, an Admin or the superuser, as `get_current_admin_user` in
 * backend/api/deps.py allows.
 */
export function isAdministrator(user: Pick<User, "role" | "is_superuser">): boolean {
  return (
    user.is_superuser ||
    user.role === UserRole.OWNER ||
    user.role === UserRole.ADMIN
  );
}

/** Only an Owner may create an Owner or give someone the Owner role. */
export function canGrantOwner(viewerRole: UserRole | null): boolean {
  return viewerRole === UserRole.OWNER;
}

/**
 * Whether the viewer may change the target's role or delete the account: an
 * Owner's account can be changed or deleted only by an Owner
 * (backend/api/v1/endpoints/users.py).
 */
export function canManageAccount(
  viewerRole: UserRole | null,
  target: Pick<User, "role">,
): boolean {
  return canGrantOwner(viewerRole) || target.role !== UserRole.OWNER;
}
