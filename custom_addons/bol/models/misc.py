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


def _normalize_barcode_for_bol(barcode):
    """Pad 11/12-digit barcode to 13-digit EAN for Bol export.
    Returns None for falsy / non-numeric input."""
    if not barcode:
        return None
    barcode = str(barcode).strip()
    if not barcode.isdigit():
        return None
    length = len(barcode)
    if length == 13:
        return barcode
    if length == 12:
        return '0' + barcode
    if length == 11:
        return '00' + barcode
    if length == 14 and barcode.startswith('0'):
        return barcode[1:]
    return barcode


def _get_possible_barcodes(barcode):
    """Generate barcode variants by stripping leading zeros one-by-one.
    First entry is the original; subsequent entries are progressively de-padded."""
    if not barcode:
        return []
    barcode = str(barcode).strip()
    if not barcode:
        return []
    variants = [barcode]
    current = barcode
    while current.startswith('0') and len(current) > 1:
        current = current[1:]
        if current and current not in variants:
            variants.append(current)
    return variants
