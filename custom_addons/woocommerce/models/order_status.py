from odoo import models, fields

STATUS_SELECTION = [('pending', 'Pending Payment'),
                    ('processing', 'Processing'),
                    ('on-hold', 'On-Hold'),
                    ('completed', 'Completed'),
                    ('cancelled', 'Cancelled'),
                    ('refunded', 'Refunded')]


class WooOrderStatus(models.Model):
    _name = 'woo.order.status'
    _description = "WooCommerce Order Status"

    name = fields.Char("Name", required=True)
    status = fields.Selection(STATUS_SELECTION, "Status")
