from sqlalchemy import text
from app.db.session import engine
def main():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT 1")).scalar()
        print("SELECT 1 ->", res)
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            )
        ).fetchall()
        print("Tables:", [r[0] for r in tables])
if __name__ == "__main__":
    main()
