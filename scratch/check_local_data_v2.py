import os
import sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from tender_app.db import get_db_connection

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Check award counts for non-framework > 250m
    cur.execute("SELECT COUNT(*) FROM contract_awards WHERE contract_value > 250000000 AND (is_framework IS NULL OR is_framework = 0);")
    c_250m_non_fw = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM contract_awards WHERE contract_value > 250000000;")
    c_250m_total = cur.fetchone()[0]

    # Let's check distribution around 250m or if contract_value ceiling logic in the app uses something else
    cur.execute("SELECT COUNT(*) FROM contract_awards WHERE contract_value >= 250000000 AND (is_framework IS NULL OR is_framework = 0);")
    c_250m_gte = cur.fetchone()[0]

    # Let's check max values and quantiles
    print(f"Non-framework awards > 250m (250,000,000): {c_250m_non_fw}")
    print(f"Total awards > 250m: {c_250m_total}")

    # 2. QA Check against market_engagements (columns organisation, title) and users (zz_%)
    cur.execute("""
        SELECT COUNT(*) FROM market_engagements 
        WHERE organisation ILIKE '%camden%' 
           OR title ILIKE '%qa test crm replacement%' 
           OR organisation ILIKE '%qa test%'
           OR title ILIKE '%delete me%';
    """)
    qa_me_count = cur.fetchone()[0]

    cur.execute("""
        SELECT id, organisation, title FROM market_engagements 
        WHERE organisation ILIKE '%camden%' 
           OR title ILIKE '%qa test crm replacement%' 
           OR organisation ILIKE '%qa test%'
           OR title ILIKE '%delete me%';
    """)
    qa_me_rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM users WHERE username ILIKE 'zz_%' OR email ILIKE 'zz_%';")
    qa_users_count = cur.fetchone()[0]

    cur.execute("SELECT id, username, email FROM users WHERE username ILIKE 'zz_%' OR email ILIKE 'zz_%';")
    qa_users_rows = cur.fetchall()

    print(f"\nQA Market Engagements count: {qa_me_count}")
    print(f"QA Market Engagements sample: {qa_me_rows}")
    print(f"QA Users count (zz_%): {qa_users_count}")
    print(f"QA Users sample: {qa_users_rows}")

if __name__ == "__main__":
    main()
