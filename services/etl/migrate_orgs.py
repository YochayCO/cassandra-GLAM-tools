import pathlib
import sys
from etl.glams_table import get_active_glams, get_glam_database_connection

SQL = pathlib.Path(__file__).with_name("SQL").joinpath("incremental.sql").read_text()


def main():
    for glam in get_active_glams():
        conn = get_glam_database_connection(glam["database"])
        try:
            cur = conn.cursor()
            for stmt in SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            print("migrated", glam["name"])
        except Exception as e:
            print("failed", glam["name"], e, file=sys.stderr)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
