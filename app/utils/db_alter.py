import psycopg2
import os
from dotenv import load_dotenv

load_dotenv('c:/Users/BOLAJI/OneDrive/Desktop/school management app/backend/.env')
db_url = os.getenv('DATABASE_URL')
print("Connecting to:", db_url)

conn = psycopg2.connect(db_url)
cur = conn.cursor()
try:
    # Add column status
    cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'Success'")
    conn.commit()
    print("Column 'status' added/verified in 'payments' table.")
    
    # Check current columns
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'payments'")
    for row in cur.fetchall():
        print(row)
except Exception as e:
    print("Error:", e)
    conn.rollback()
finally:
    cur.close()
    conn.close()
