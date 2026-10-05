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

# table name -> {column name -> SQLite column type}, for every column added
# to a model that a database file created before this round wouldn't have
# yet. Add a new column under the right table (or a new table entry) any
# time a model gains a field that needs to show up in an existing,
# already-running database.
NEW_COLUMNS = {
    "report": {
        "engineer_cause": "TEXT",
        "ai_summary": "TEXT",
        "project_id": "INTEGER",
        "video_all_frames_json": "TEXT",
        "extra_photos_json": "TEXT",
        "photo_results_json": "TEXT",
        "engineer_crack_presence": "TEXT",
        "engineer_crack_type": "TEXT",
        "engineer_severity": "TEXT",
        "verification_note": "TEXT",
        "verified_at": "DATETIME",
    },
    "user": {
        "cover_photo_path": "TEXT",
    },
}

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

added = []
for table, columns in NEW_COLUMNS.items():
    cur.execute(f"PRAGMA table_info({table})")
    existing_columns = {row[1] for row in cur.fetchall()}
    for name, col_type in columns.items():
        if name not in existing_columns:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
            added.append(f"{table}.{name}")

conn.commit()
conn.close()

if added:
    print(f"Added missing column(s): {', '.join(added)}")
else:
    print("Nothing to do -- all columns already exist.")
