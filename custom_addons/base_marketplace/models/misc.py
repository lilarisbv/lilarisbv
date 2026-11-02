import functools
import hashlib
import json
import logging
import re

from odoo.http import db_list
from odoo.tools.mimetypes import _mime_mappings

from odoo.addons.base_marketplace.models.exceptions import MarketplaceGoLiveSkip

_logger = logging.getLogger("Teqstars:Base Marketplace")

_MARKETPLACE_DB_TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')


def marketplace_db_token(db_name):
    """
    Task: T9113 - Build the token that stands for a database name in public marketplace URLs.
        It is the first 32 hex characters of the SHA-256 of the name (same as the eBay connector), so the URL no longer
        shows the database name.
    Args:
        db_name (str): The database name.
    Returns:
        str: The 32 character token.
    """
    return hashlib.sha256(db_name.encode()).hexdigest()[:32]


def resolve_marketplace_db_name(db_value, host=None):
    """
    Task: T9113 - Find the database a public marketplace URL (webhook or image) points to.
        The URL segment holds either the plain database name, as every URL built before the token existed does, or its
        token. The plain name is tried first, so existing URLs resolve exactly as before. Only the databases that pass
        the operator's db_filter for this host are considered, because db_list already applies it.
    Args:
        db_value (str): The database segment taken from the URL.
        host (str, optional): The HTTP host of the request, used by db_filter.
    Returns:
        str or bool: The database name, or False when the segment matches no permitted database.
    """
    if not db_value:
        return False
    db_names = db_list(force=True, host=host)
    if db_value in db_names:
        return db_value
    if _MARKETPLACE_DB_TOKEN_RE.match(db_value):
        for db_name in db_names:
            if marketplace_db_token(db_name) == db_value:
                return db_name
    return False


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
                        # a warning, then the cron is skipped through MarketplaceGoLiveSkip.
                        mk_instance_id.redirect_instance_for_go_live(mk_instance_id)
                        if self.env.context.get('cron_id') or self.env.context.get('ir_cron_progress_id'):
                            raise MarketplaceGoLiveSkip(f"Go Live is off for the instance {mk_instance_id.name}, cron skipped.")
                        return {}
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
