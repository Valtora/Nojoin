# Git Commit Description - Use Conventional Commit Guidelines

docs: use docker-compose.example.yml workflow

BREAKING CHANGE: The `docker-compose.yml` file is no longer tracked in git. Users must create their own `docker-compose.yml` by copying `docker-compose.example.yml`. This allows for local customization without conflicts on `git pull`.

- Add `docker-compose.yml` and `docker-compose.yaml` to `.gitignore`.
- Rename project `docker-compose.yml` to `docker-compose.example.yml` (handled by user/git).
- Update `README.md` and `docs/DEPLOYMENT.md` with new setup instructions.
