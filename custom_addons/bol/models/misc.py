import logging
import traceback

import dateutil
from pytz import timezone

_logger = logging.getLogger("TeqStars:bol")


def convert_bol_datetime_to_utc(datetime):
    converted_datetime = ""
    if datetime:
        datetime = dateutil.parser.parse(datetime)
        converted_datetime = datetime.astimezone(timezone('UTC')).strftime('%Y-%m-%d %H:%M:%S')
    return converted_datetime or False


def log_traceback_for_exception():
    _logger.error(traceback.format_exc())
