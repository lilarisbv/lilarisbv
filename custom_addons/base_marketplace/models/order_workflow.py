from odoo import models, fields, api


class OrderWorkflowConfig(models.Model):
    _name = "order.workflow.config.ts"
    _description = "Order Workflow Configuration"

    def _get_default_journal(self):
        if self.env.context.get('default_journal_type'):
            return self.env['account.journal'].search([('company_id', '=', self.env.user.company_id.id),
                                                       ('type', '=', self.env.context['default_journal_type'])],
                limit=1).id

    @api.depends('journal_id')
    def _compute_payment_method_line_fields(self):
        for workflow in self:
            if workflow.journal_id:
                workflow.available_payment_method_line_ids = workflow.journal_id._get_available_payment_method_lines('inbound')
            else:
                workflow.available_payment_method_line_ids = False

    @api.depends('available_payment_method_line_ids')
    def _compute_payment_method_line_id(self):
        for pay in self:
            available_payment_method_lines = pay.available_payment_method_line_ids

            # Select the first available one by default.
            if pay.payment_method_line_id in available_payment_method_lines:
                pay.payment_method_line_id = pay.payment_method_line_id
            elif available_payment_method_lines:
                pay.payment_method_line_id = available_payment_method_lines[0]._origin
            else:
                pay.payment_method_line_id = False

    name = fields.Char("Name", required=True, translate=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    is_confirm_order = fields.Boolean("Confirm Order", default=False)
    is_lock_order = fields.Boolean("Lock Confirmed Order", default=False, help="No longer edit orders once confirmed")

    is_create_invoice = fields.Boolean('Create Invoice', default=False)
    is_force_payment_reference = fields.Boolean(string="Force Payment Reference", default=False,
                                                help="If enabled, the marketplace order number will be used as the payment reference on the invoice. This helps with reconciliation of payments.")
    is_validate_invoice = fields.Boolean(string='Validate Invoice', default=False)
    is_register_payment = fields.Boolean(string='Register Payment', default=False)

    journal_id = fields.Many2one('account.journal', string='Payment Journal')
    sale_journal_id = fields.Many2one('account.journal', string='Order Journal', default=_get_default_journal)
    force_invoice_date = fields.Boolean(string='Force Invoice Date', default=False, help="Set Invoice date and Payment date same as Order date.")
    payment_method_line_id = fields.Many2one('account.payment.method.line', string='Payment Method', readonly=False, store=True, compute='_compute_payment_method_line_id', domain="[('id', 'in', available_payment_method_line_ids)]", help="")
    available_payment_method_line_ids = fields.Many2many('account.payment.method.line', compute='_compute_payment_method_line_fields')

    picking_policy = fields.Selection([('direct', 'Deliver each product when available'), ('one', 'Deliver all products at once')], string='Shipping Policy', default='direct',
        help="If you deliver all products at once, the delivery order will be scheduled based on the greatest product lead time. Otherwise, it will be based on the shortest.")
