from etl.views import load_views_for_date
import logging
import os
from datetime import date, timedelta, datetime
from lib.sentry import with_sentry
from etl.glams_table import close_glams_connections, get_active_glams, open_glams_connections
from etl.etl_glam import process_glam
from etl.download_mediacounts import get_nfs_file_path


def process_glams(glams):
    for glam in glams:
        logging.info(f" {datetime.now()} Processing glam: {glam['name']}")
        process_glam(glam)


def main(date_val: date):
    glams = get_active_glams()

    if glams == None:
        logging.info(f" {datetime.now()} No active glams found")
        return

    logging.info(f" {datetime.now()} Processing {len(glams)} glams: {', '.join(map(lambda glam: glam['name'] ,glams))}")

    process_glams(glams)
    filepath = get_nfs_file_path(date_val)
    open_glams_connections(glams)
    try:
        load_views_for_date(glams, filepath, date_val)
    finally:
        close_glams_connections(glams)


if __name__ == "__main__":
    if not os.path.exists("Logs"):
        os.makedirs("Logs")
    logging.basicConfig(filename=f"Logs/cronjob_{date.today().strftime('%Y-%m-%d')}.log", filemode='a', level=logging.INFO, force=True)
    yesterday_date = date.today() - timedelta(days=1)
    logging.info(f" {datetime.now()} Starting daily tasks for {yesterday_date}")
    with_sentry()
    main(yesterday_date)
    logging.info(f' {datetime.now()} Done daily tasks for {yesterday_date}')
