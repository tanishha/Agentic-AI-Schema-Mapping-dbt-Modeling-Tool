# Shared Migration Database

DBMapper writes the codebase-level database here:

```powershell
data/database/project.db
```

Session databases are recreated for each run. `project.db` keeps existing rows
and uses `INSERT OR REPLACE` on primary-key conflicts, so new primary keys
accumulate and repeated primary keys are replaced by the latest migration.

Useful commands:

```powershell
sqlite3 data/database/project.db ".tables"
sqlite3 data/database/project.db ".schema"
sqlite3 data/database/project.db < data/database/scripts/select_all.sql
sqlite3 data/database/project.db < data/database/scripts/delete_all_data.sql
```

If the `sqlite3` command is not installed, use the Python runner:

```powershell
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql

# Source/final tables only
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/source_table_counts.sql

# dbt staging and mart objects only
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/dbt_table_counts.sql
```

To run the same scripts against a session-specific DB:

```powershell
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql --db data/uploads/<session-id>/migration.db
```

Each upload session also gets its own copy:

```powershell
data/uploads/<session-id>/migration.db
data/uploads/<session-id>/report.json
```
