# Database

SQLite database operations.

## Capabilities

- **Query**: SELECT, INSERT, UPDATE, DELETE
- **Schema**: Create tables, indexes
- **Export**: CSV, JSON dump
- **Backup**: Copy database files

## Commands

```bash
# Open SQLite CLI
sqlite3 database.db

# Run query
sqlite3 database.db "SELECT * FROM users"

# Import CSV
sqlite3 database.db ".mode csv" ".import data.csv tablename"

# Export to CSV
sqlite3 -header -csv database.db "SELECT * FROM users" > users.csv

# Backup
sqlite3 database.db ".backup backup.db"

# Schema info
sqlite3 database.db ".schema"
sqlite3 database.db ".tables"
```

## Examples

"Query this SQLite database"
"Export this table to CSV"
"Show the schema of this database"
"Create a backup of the database"
