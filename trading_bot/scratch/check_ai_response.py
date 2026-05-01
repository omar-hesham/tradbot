import sqlite3
import os

db_path = 'data/trading_bot.db'

def check_last_scanner():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT raw_response, timestamp FROM ai_decisions WHERE parsed_action = 'SCANNER' ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            print(f"--- Last SCANNER Response ({row[1]}) ---")
            print(row[0])
            print("-" * 50)
        else:
            print("No SCANNER decisions found.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_last_scanner()
