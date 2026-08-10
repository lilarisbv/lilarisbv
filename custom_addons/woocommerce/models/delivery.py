from odoo import fields, models


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    woocommerce_code = fields.Char(string='WooCommerce Carrier Code')
