# Release Notes - Postgres 18 Migration

## ⚠️ Action Required: Database Migration

This release updates the database from PostgreSQL 16 to PostgreSQL 18 (pg18-trixie) to leverage the latest performance improvements.

**You must perform a manual migration** or your application will fail to start.

### Migration Guide (Docker)

If you are running Nojoin via Docker Compose, follow these steps to migrate your data.

#### Method 1: Built-in Backup (Recommended)

This method is the easiest and safest.

1. **Before upgrading**, Ensure your current Nojoin instance (running `pg16`) is up.
2. Go to **Settings > Backup & Restore**.
3. Click **Create Backup** and download the `.zip` file.
4. Stop your containers and **delete the database volume**:

   ```bash
   docker compose down -v
   ```

   _(Note: This deletes your `postgres_data` volume. Make sure you have your backup!)_

5. Pull the new images:

   ```bash
   docker compose pull
   ```

6. Start the application (it will create a fresh, empty Postgres 18 database):

   ```bash
   docker compose up -d
   ```

7. Go to **Settings > Backup & Restore** and upload your `.zip` backup to restore your data.

#### Method 2: Manual Dump & Restore (Advanced)

If you prefer not to use the built-in system or cannot access the UI.

1. **Dump your data** from the running container:

   ```bash
   docker exec -t nojoin-db pg_dumpall -c -U postgres > dump_prev.sql
   ```

2. Stop and remove volumes:

   ```bash
   docker compose down -v
   ```

3. Update/Pull images and start:

   ```bash
   docker compose pull
   docker compose up -d
   ```

4. **Restore the dump**:

   ```bash
   cat dump_prev.sql | docker exec -i nojoin-db psql -U postgres
   ```

   > **Note:** If you see "relation already exists" errors, it means the database was initialized before restore. This is expected if the container started up and ran migrations. Data should still be restored.
   > **Important:** While `pg_dumpall` captures the database schema and data, **Nojoin's application-level backup (Method 1) is preferred** as it handles file attachments (recordings) and ensures data consistency better than raw SQL dumps. Use Method 2 only if necessary.

### Volume Configuration Change

Postgres 18+ images require mounting the volume at `/var/lib/postgresql` instead of `/var/lib/postgresql/data`. The `docker-compose.yml` has been updated to reflect this:

```yaml
volumes:
  - postgres_data:/var/lib/postgresql
```

Ensure you update your configuration to avoid startup errors.

### Compatibility Note

- Backups created with the built-in system on previous versions (Postgres 16/17) **are fully compatible** with the new version.
