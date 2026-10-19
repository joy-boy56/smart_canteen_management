"""Initialize Smart Canteen SQLite database schema and default settings."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import get_database_path, init_db


def main() -> None:
    db_path = get_database_path()
    init_db(db_path)
    print(f"[OK] Smart Canteen database initialized at: {db_path}")


if __name__ == "__main__":
    main()
