import argparse
import sqlite3
from pathlib import Path


def split_sql(script: str) -> list[str]:
    lines = [
        line for line in script.splitlines()
        if not line.lstrip().startswith(".")
    ]
    cleaned = "\n".join(lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


def print_rows(cursor: sqlite3.Cursor):
    rows = cursor.fetchall()
    if cursor.description is None:
        print(f"rows affected: {cursor.rowcount}")
        return
    columns = [desc[0] for desc in cursor.description]
    print(" | ".join(columns))
    print("-+-".join("-" * len(col) for col in columns))
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def main():
    parser = argparse.ArgumentParser(description="Run a SQL script against a SQLite database.")
    parser.add_argument("script", help="Path to .sql file")
    parser.add_argument("--db", default="data/database/project.db", help="SQLite database path")
    args = parser.parse_args()

    db_path = Path(args.db)
    script_path = Path(args.script)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    if not script_path.exists():
        raise SystemExit(f"SQL script not found: {script_path}")

    conn = sqlite3.connect(db_path)
    try:
        for statement in split_sql(script_path.read_text()):
            print(f"\n> {statement}")
            cursor = conn.execute(statement)
            print_rows(cursor)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
