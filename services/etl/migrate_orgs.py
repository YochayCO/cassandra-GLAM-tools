import pathlib
import sys
from etl.glams_table import get_active_glams, get_glam_database_connection

SQL_DIR = pathlib.Path(__file__).with_name("SQL")
SQL = SQL_DIR.joinpath("incremental.sql").read_text()
ADD_CATEGORY_SQL = SQL_DIR.joinpath("add_category.sql").read_text()


def main():
    for glam in get_active_glams():
        conn = get_glam_database_connection(glam["database"])
        try:
            cur = conn.cursor()
            for stmt in SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            cur.execute(ADD_CATEGORY_SQL)
            print("migrated", glam["name"])
        except Exception as e:
            print("failed", glam["name"], e, file=sys.stderr)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
