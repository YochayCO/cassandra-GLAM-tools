import pathlib
import subprocess
import sys
from etl.glams_table import get_active_glams, get_glam_database_connection

SETUP_JS = pathlib.Path(__file__).resolve().with_name("setup.js")
APPLY_FILES = ("incremental.sql", "functions.sql", "maintenance.sql")


def drop_view_or_matview(cur, name):
    cur.execute(
        "SELECT c.relkind FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = %s",
        (name,),
    )
    row = cur.fetchone()
    if not row:
        return
    kind = row[0]
    if kind == "m":
        cur.execute(f"DROP MATERIALIZED VIEW {name}")
    elif kind == "v":
        cur.execute(f"DROP VIEW {name}")


def apply_sql_files(glam_name):
    subprocess.run(
        ["node", str(SETUP_JS), glam_name, *APPLY_FILES],
        check=True,
    )


def main():
    failed = False
    for glam in get_active_glams():
        conn = get_glam_database_connection(glam["database"])
        try:
            cur = conn.cursor()
            drop_view_or_matview(cur, "visualizations_stats")
            drop_view_or_matview(cur, "visualizations_sum")
            apply_sql_files(glam["name"])
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
