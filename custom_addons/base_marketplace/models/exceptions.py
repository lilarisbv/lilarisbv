# -*- coding: utf-8 -*-

from odoo.tools import safe_eval


class MarketplaceException(Exception):
    """Specific exception subclass for marketplace related errors"""

    def __init__(self, message, title=None, additional_context=None):
        super().__init__(message, title, additional_context)


safe_eval._BUBBLEUP_EXCEPTIONS = (
        safe_eval._BUBBLEUP_EXCEPTIONS + (MarketplaceException,)
)
