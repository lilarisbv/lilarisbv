from odoo import fields, models
from odoo.addons.bol.models.bol_invoice_request import INVOICE_STATUS_TRANSITION


class BolInvoiceStatusTransitionsLine(models.Model):
    _name = 'bol.invoice.status.transitions.line'
    _description = "Bol Invoice Status Transitions Line"
    _order = 'date desc'

    date = fields.Datetime(string="Date", help="Transitions date")
    status = fields.Selection(INVOICE_STATUS_TRANSITION, string="Status", help="Current invoice processing status at the time of this transition.")
    request_id = fields.Many2one('bol.invoice.request', string="Invoice Request", required=True, ondelete='cascade',
                                 help="The invoice request to which this status transitions belongs.")
