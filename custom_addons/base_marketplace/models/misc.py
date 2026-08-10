import functools
import json
import logging

from odoo.tools.mimetypes import _mime_mappings

_logger = logging.getLogger("Teqstars:Base Marketplace")


def check_go_live(instance_arg=None):
    """
    Task: T7423 - Block marketplace API operations when the database is neutralized
        and the instance has not enabled Go Live.

    Wrap any per-marketplace request/operation method. Before the wrapped method
    runs, it resolves the `mk.instance` record and, if the database is neutralized
    (`is_neutralized=True`) while Go Live is off (`go_live=False`), it stops the
    operation and redirects the user to the instance form to enable Go Live.

    Usage:
        @check_go_live()                  # self is mk.instance
        def _send_bol_request(self, ...): ...

        @check_go_live('mk_instance_id')  # self carries mk_instance_id
        def shopify_export_listing_to_mk(self, ...): ...

        @check_go_live(1)                 # instance is positional arg #1
        def shopify_import_orders(self, mk_instance_ids, ...): ...

    Args:
        instance_arg (str|int|None): How to locate the `mk.instance` record.
            - None  -> use `self` (the recordset the method is called on).
            - str   -> read the keyword arg/attribute/field of that name.
            - int   -> take it from positional args (index in *args, 0-based,
                       not counting `self`).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # During module install/upgrade/migration the registry is not ready
            # yet. Allow the API request through so migration scripts can sync and
            # update marketplace data even on a neutralized database.
            if not self.env.registry.ready:
                return func(self, *args, **kwargs)
            # Callers can explicitly bypass the Go Live gate via context (e.g. resetting
            # an instance to draft, which must still clean up remote webhooks even when
            # Go Live is off on a neutralized database).
            if self.env.context.get('skip_instance_go_live', False):
                return func(self, *args, **kwargs)
            instance = None
            if instance_arg is None:
                instance = self
            elif isinstance(instance_arg, str):
                instance = kwargs.get(instance_arg) or getattr(self, instance_arg, None)
            elif isinstance(instance_arg, int) and len(args) > instance_arg:
                instance = args[instance_arg]
            if instance is not None and getattr(instance, '_name', False) == 'mk.instance':
                for mk_instance_id in instance:
                    if mk_instance_id.is_neutralized and not mk_instance_id.go_live:
                        # On UI this raises (redirect popup); during cron it only logs
                        # a warning and returns False, so we skip the API request.
                        mk_instance_id.redirect_instance_for_go_live(mk_instance_id)
                        return False
            return func(self, *args, **kwargs)
        return wrapper

    return decorator


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
