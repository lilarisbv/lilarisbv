from odoo.exceptions import RedirectWarning
from odoo.http import request

from odoo import models


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
