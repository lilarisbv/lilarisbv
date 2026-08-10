from odoo import models, fields
import logging

_logger = logging.getLogger("Teqstars:WooCommerce")


class ListingImage(models.Model):
    _inherit = 'mk.listing.image'

    woo_alt_text = fields.Char("alt text")