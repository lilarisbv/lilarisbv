from odoo import models, fields, api, _


class MKCancelOrder(models.TransientModel):
    _name = "mk.cancel.order"
    _description = "Cancel Order In Marketplace"

    is_create_refund = fields.Boolean("Create Refund?", default=False, help="Weather to Create Refund/Credit Note in Odoo?")
    refund_description = fields.Char("Refund Reason")
    date_invoice = fields.Date(string='Credit Note Date', default=fields.Date.context_today, required=True)
    payment_journal_id = fields.Many2one('account.journal', string='Payment Journal', domain=[('type', 'in', ('bank', 'cash'))])
    currency_id = fields.Many2one('res.currency', string='Currency')
    create_refund_option_visible = fields.Boolean("Allow Credit Note Creation", help="Technical field to identify user can create credit note or not.")
