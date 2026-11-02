import logging
import re

import odoo.http
from odoo.exceptions import RedirectWarning
from odoo.http import request

from odoo import models
from odoo.addons.base_marketplace.models.misc import resolve_marketplace_db_name

_logger = logging.getLogger("Teqstars:Base Marketplace")

# Task: T9113 - Extracts the db_name path segment from any connector's webhook or product-image
# route (e.g. /shopify/webhook/notification/<db_name>/..., /bol/webhook/notification/<db_name>/...,
# /marketplace/product/image/<db_name>/...). The connector name is left open ([a-z0-9_]+) so a new
# connector's route is covered automatically; the part after it is matched exactly so unrelated
# routes such as Odoo's own /web/image/... are never mistaken for one of these.
_MARKETPLACE_DB_PATH_RE = re.compile(
    r'^/[a-z0-9_]+/(?:'
    r'webhook/notification'
    r'|product/image'
    r'|collections/image'
    r')/([^/?#]+)/'
)


def _install_marketplace_db_path_patch():
    """
    Task: T9113 - Patch Odoo's request database resolution to support marketplace database paths.
        The patch checks whether the request URL contains a marketplace database name or its token. If one is found, it is used only when the database exists and
        passes Odoo's configured database filter. Otherwise, the original Odoo database name is preserved. Any unexpected error during the custom resolution
        is logged and falls back to the original database name. The patch is applied only once to avoid wrapping the same Odoo method multiple times.
    Args:
        None.
    Returns:
        None.
    """
    if getattr(odoo.http.Request._get_session_and_dbname, '_mk_patched', False):
        return

    _original = odoo.http.Request._get_session_and_dbname

    def _patched(self):
        session, dbname = _original(self)
        try:
            path = self.httprequest.path or ''
            db_name_match = _MARKETPLACE_DB_PATH_RE.match(path)
            if not db_name_match:
                return session, dbname
            db_segment = db_name_match.group(1)
            if db_segment == dbname:
                return session, dbname
            # Task: T9113 - The segment is either the database name or its token; both resolve to a permitted database name.
            resolved_db_name = resolve_marketplace_db_name(db_segment, host=self.httprequest.environ.get('HTTP_HOST'))
            if not resolved_db_name or resolved_db_name == dbname:
                return session, dbname
            return session, resolved_db_name
        except Exception:
            _logger.exception("Marketplace DB-from-URL resolution failed; using stock dbname=%r", dbname)
            return session, dbname

    _patched._mk_patched = True
    odoo.http.Request._get_session_and_dbname = _patched


_install_marketplace_db_path_patch()


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _dispatch(cls, endpoint):
        """
        Task: T7423 - Force the Go Live redirect popup, outside every connector try/except.

        `redirect_instance_for_go_live` raises a RedirectWarning to block import/export
        when the database is neutralized and Go Live is off. Some connectors wrap their
        API calls in `except Exception` and swallow that RedirectWarning, so the popup
        never reaches the user. `_dispatch` wraps the whole endpoint call - outside every
        connector try/except - so here we re-raise the redirect (stashed on the request
        by redirect_instance_for_go_live) after the endpoint returns, guaranteeing the
        popup regardless of connector exception handling.
        Args:
            endpoint (object): The matched controller endpoint to be dispatched.
        Returns:
            object: The endpoint result when no Go Live redirect was stashed on the request.
        Raises:
            RedirectWarning: When the Go Live guard fired on this request, to force the
                instance redirect popup regardless of connector exception handling.
        """
        # Clear any stale flag from a previous request reusing this object.
        if request is not None and hasattr(request, 'mk_go_live_redirect'):
            del request.mk_go_live_redirect
        try:
            result = super()._dispatch(endpoint)
        except Exception:
            # The connector either let the guard's RedirectWarning bubble or caught
            # and re-raised it as another exception. Either way, if the guard fired
            # this request, force the redirect popup instead of the connector error.
            if getattr(request, 'mk_go_live_redirect', False):
                error_msg, action_data, button_text = request.mk_go_live_redirect
                del request.mk_go_live_redirect
                raise RedirectWarning(error_msg, action_data, button_text)
            raise
        # The connector swallowed the guard's RedirectWarning and returned normally;
        # force the redirect popup so the blocked operation is still surfaced.
        if getattr(request, 'mk_go_live_redirect', False):
            error_msg, action_data, button_text = request.mk_go_live_redirect
            del request.mk_go_live_redirect
            raise RedirectWarning(error_msg, action_data, button_text)
        return result
