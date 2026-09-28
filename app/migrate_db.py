# One-time database update -- adds any columns the Report model has gained
# that aren't in your actual infracracknet.db file yet, WITHOUT deleting or
# changing anything already in it (your existing reports, users, everything
# stays exactly as it is). Safe to run more than once -- it only adds a
# column if it's genuinely missing, and does nothing otherwise.
#
# Run this once from inside your Code/app folder:
#     python migrate_db.py
#
# You should see either "Added missing column(s): ai_summary" (first time)
# or "Nothing to do" (if you run it again later, or already had it).

import sqlite3

DB_PATH = "infracracknet.db"

# name -> SQLite column type, for every column added to the Report model
# that a database file created before this round wouldn't have yet.
NEW_COLUMNS = {
    "engineer_cause": "TEXT",
    "ai_summary": "TEXT",
}

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("PRAGMA table_info(report)")
existing_columns = {row[1] for row in cur.fetchall()}

added = []
for name, col_type in NEW_COLUMNS.items():
    if name not in existing_columns:
        cur.execute(f"ALTER TABLE report ADD COLUMN {name} {col_type}")
        added.append(name)

conn.commit()
conn.close()

if added:
    print(f"Added missing column(s): {', '.join(added)}")
else:
    print("Nothing to do -- all columns already exist.")
