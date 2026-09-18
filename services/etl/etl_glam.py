import logging
import os
import subprocess
from datetime import datetime, timedelta, date
from subprocess import SubprocessError
from .glams_table import update_to_failed, update_to_running

if not os.path.exists("Logs"):
    os.makedirs("Logs")
logging.basicConfig(filename=f"Logs/cronjob_{date.today().strftime('%Y-%m-%d')}.log", filemode='a', level=logging.INFO, force=True)

def _etl(name):
    r = subprocess.run(
        ['node', '--max-old-space-size=5120', f'{__package__}/etl.js', name],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.stdout:
        logging.info(r.stdout[-4000:])
    if r.returncode != 0:
        logging.error('etl.js stderr: %s', r.stderr[-8000:] if r.stderr else '')
        logging.error('etl.js returncode %s', r.returncode)
        err = subprocess.CalledProcessError(r.returncode, r.args)
        err.stderr = r.stderr
        raise err


def process_glam(glam):
    if glam['lastrun'] != None and datetime.utcnow() < glam['lastrun'].replace(tzinfo=None) + timedelta(days=1):
        logging.info(' %s Glam %s is already updated', datetime.now(), glam['name'])
        return

    success = True
    logging.info(f' {datetime.now()} Processing glam: {glam["name"]}')

    try:
        _etl(glam['name'])
    except SubprocessError as e:
        success = False
        logging.error('Subprocess etl.js failed')

        if e.returncode == 65:
            logging.error('Glam %s is now failed', glam['name'])
            update_to_failed(glam)
            return

    if success:
        logging.info(' %s Completed scheduler for %s',datetime.now() ,glam['name'])
        update_to_running(glam)
    else:
        logging.error(' %s Failed scheduler for %s',datetime.now(), glam['name'])
