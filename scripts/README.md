# Dental administrator bootstrap

This is a manually invoked operator utility, not a GitHub Actions workflow. It is
intentionally not connected to automatic deployment hooks.

Install the project dependencies and make sure the current working directory is
the repository root. Set these values in the approved operator environment:

- `DATABASE_URL`: the intended Dental PostgreSQL database connection string.
- `DENTAL_MASTER_CLINIC_ID`: the exact clinic identifier for a newly created admin.
- `DENTAL_BOOTSTRAP_ADMIN_PASSWORD`: a unique password of at least 12 characters.

Do not put the password in command-line arguments, commit it, or print it in logs.

Run the migration and create the administrator:

```bash
python scripts/bootstrap_admin.py --confirm-production --migrate
```

If the account already exists, the script deliberately refuses to change its
credentials unless password reset is explicitly requested:

```bash
python scripts/bootstrap_admin.py --confirm-production --migrate --reset-existing
```

The reset path updates the existing account's scrypt password hash, enables the
account, and assigns the admin role while preserving its existing clinic ID.
The create path assigns the configured clinic ID. The script never prints the
password or database URL.

Review the target database and account before execution. This utility does not
run automatically, does not configure Render environment variables, and does
not trigger a deployment. A separate approved deployment step is required after
the operator has completed and verified the migration.
