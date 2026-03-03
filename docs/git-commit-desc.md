# Git Commit Description - Use Conventional Commit Guidelines

fix(auth): restore companion app pairing after HttpOnly cookie migration

The v0.6.4 HttpOnly cookie migration removed the JWT from localStorage,
but authorizeCompanion() still read from localStorage.getItem("token"),
which was always null. The companion's /auth endpoint was never reached.

- Add GET /login/companion-token backend endpoint that issues a fresh JWT
  for companion app pairing, authenticated via HttpOnly cookie so the
  token never needs to reside in localStorage
- Rewrite authorizeCompanion() in serviceStatusStore.ts to fetch the JWT
  from the new endpoint using fetch() with credentials: "include",
  bypassing the axios 401 interceptor
- Add getCompanionToken() export to api.ts
- Fix get_current_user_ws() in deps.py to decode actual_token instead of
  the raw token query param, correcting cookie-based WebSocket auth
