import sqlite3

conn = sqlite3.connect("data/media.db")
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print("Tables:", tables)

for t in tables:
    cursor = conn.execute(f"SELECT * FROM {t}")
    cols = [d[0] for d in cursor.description]
    print(f"\nTable [{t}] columns: {cols}")
    for row in cursor.fetchall():
        print(dict(zip(cols, row)))

conn.close()
