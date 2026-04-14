import functools
import json
import logging
_logger = logging.getLogger("Teqstars:Base Marketplace")

from odoo.tools.mimetypes import _mime_mappings


def process_response(response):
    # Function to process and format the GraphQL response dynamically
    def traverse_json(data):
        if isinstance(data, dict):
            if 'edges' in data:
                return [traverse_json(edge['node']) for edge in data['edges']]
            result = {}
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    result[key] = traverse_json(value)
                else:
                    result[key] = value
            return result
        elif isinstance(data, list):
            return [traverse_json(item) for item in data]
        else:
            return data

    return traverse_json(json.loads(response))


# This is added because removed 'text/plain' because binary field value so it is return from base always.
def _odoo_guess_mimetype(bin_data, default='application/octet-stream'):
    """ Attempts to guess the mime type of the provided binary data, similar
    to but significantly more limited than libmagic

    :param str bin_data: binary data to try and guess a mime type for
    :returns: matched mimetype or ``application/octet-stream`` if none matched
    """
    # by default, guess the type using the magic number of file hex signature (like magic, but more limited)
    # see http://www.filesignatures.net/ for file signatures
    for entry in _mime_mappings:
        for signature in entry.signatures:
            if bin_data.startswith(signature):
                for discriminant in entry.discriminants:
                    try:
                        guess = discriminant(bin_data)
                        if guess: return guess
                    except Exception:
                        # log-and-next
                        _logger.getChild('guess_mimetype').warning(
                            "Sub-checker '%s' of type '%s' failed",
                            discriminant.__name__, entry.mimetype,
                            exc_info=True
                        )
                # if no discriminant or no discriminant matches, return
                # primary mime type
                return entry.mimetype
    return default


try:
    import magic
except ImportError:
    magic = None

if magic:
    # There are 2 python libs named 'magic' with incompatible api.
    # magic from pypi https://pypi.python.org/pypi/python-magic/
    if hasattr(magic, 'from_buffer'):
        _guesser = functools.partial(magic.from_buffer, mime=True)
    # magic from file(1) https://packages.debian.org/squeeze/python-magic
    elif hasattr(magic, 'open'):
        ms = magic.open(magic.MAGIC_MIME_TYPE)
        ms.load()
        _guesser = ms.buffer


    def guess_mimetype(bin_data, default=None):
        mimetype = _guesser(bin_data[:1024])
        # upgrade incorrect mimetype to official one, fixed upstream
        # https://github.com/file/file/commit/1a08bb5c235700ba623ffa6f3c95938fe295b262
        if mimetype == 'image/svg':
            return 'image/svg+xml'
        return mimetype
else:
    guess_mimetype = _odoo_guess_mimetype
