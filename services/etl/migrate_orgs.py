import pathlib
import re
import sys
from etl.glams_table import get_active_glams, get_glam_database_connection

SQL_DIR = pathlib.Path(__file__).with_name("SQL")
SQL = SQL_DIR.joinpath("incremental.sql").read_text()
ADD_CATEGORY_SQL = SQL_DIR.joinpath("add_category.sql").read_text()
FUNCTIONS_SQL = SQL_DIR.joinpath("functions.sql").read_text()
MAINTENANCE_SQL = SQL_DIR.joinpath("maintenance.sql").read_text()

_FUNC_END = re.compile(r"\$\$\s*language\s+plpgsql\s*;", re.I)
_FUNC_START = re.compile(r"create(?:\s+or\s+replace)?\s+function\b", re.I)


def sql_statements(sql):
    stmts = []
    rest = sql
    while True:
        m = _FUNC_END.search(rest)
        if not m:
            for s in rest.split(";"):
                s = s.strip()
                if s:
                    stmts.append(s)
            return stmts
        chunk = rest[: m.end()]
        rest = rest[m.end() :]
        starts = list(_FUNC_START.finditer(chunk))
        if not starts:
            for s in chunk.split(";"):
                s = s.strip()
                if s:
                    stmts.append(s)
            continue
        start = starts[-1].start()
        for s in chunk[:start].split(";"):
            s = s.strip()
            if s:
                stmts.append(s)
        stmts.append(chunk[start:].strip())


def apply_sql(cur, sql):
    for stmt in sql_statements(sql):
        cur.execute(stmt)


def main():
    failed = False
    for glam in get_active_glams():
        conn = get_glam_database_connection(glam["database"])
        try:
            cur = conn.cursor()
            for blob in (SQL, ADD_CATEGORY_SQL, FUNCTIONS_SQL, MAINTENANCE_SQL):
                apply_sql(cur, blob)
            print("migrated", glam["name"])
        except Exception as e:
            failed = True
            print("failed", glam["name"], e, file=sys.stderr)
        finally:
            conn.close()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
