import sqlite3

# Connect to the database
conn = sqlite3.connect("catalogue.db")
cursor = conn.cursor()

# Get all table names
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [row[0] for row in cursor.fetchall()]

for table in tables:
    print(f"\n=== TABLE: {table} ===")
    
    # Get column names
    cursor.execute(f"PRAGMA table_info('{table}')")
    columns = [col[1] for col in cursor.fetchall()]
    print("Columns:", columns)
    
    # Get total row count
    cursor.execute(f"SELECT COUNT(*) FROM '{table}'")
    row_count = cursor.fetchone()[0]
    print(f"Total Rows: {row_count}")
    
    # Get first 5 rows
    cursor.execute(f"SELECT * FROM '{table}'")
    rows = cursor.fetchall()
    print("First 5 Rows:")
    for row in rows:
        print(row)

conn.close()