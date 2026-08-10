from odoo.exceptions import RedirectWarning
from odoo.tools import safe_eval

# Odoo's RedirectWarning carries (message, action, button_text, context) in its
# args, so its default str()/repr dumps the whole tuple - including the redirect
# action dict - into any log built with `f"{e}"` or `str(e)`. Connectors catch and
# re-log it in many places, so we make str() expose only the human-readable
# message here, centrally. The frontend redirect button is unaffected: the RPC
# layer serializes `exception.args` (not str()) to render the dialog/button.
if not getattr(RedirectWarning, '_mk_clean_str', False):
    def _redirect_warning_str(self):
        return str(self.args[0]) if self.args else Exception.__str__(self)


    RedirectWarning.__str__ = _redirect_warning_str
    # Some connector `except Exception as e` blocks probe `e.obj` (e.g. WooCommerce:
    # `e.obj and str(e.obj) or e`); expose the readable message so they log/raise a
    # clean string instead of raising AttributeError.
    RedirectWarning.obj = property(lambda self: str(self.args[0]) if self.args else Exception.__str__(self))
    RedirectWarning._mk_clean_str = True


class MarketplaceException(Exception):
    """Specific exception subclass for marketplace related errors"""

    def __init__(self, message, title=None, additional_context=None):
        super().__init__(message, title, additional_context)


safe_eval._BUBBLEUP_EXCEPTIONS = (
        safe_eval._BUBBLEUP_EXCEPTIONS + (MarketplaceException,)
)
