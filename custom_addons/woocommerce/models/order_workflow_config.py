from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

ORDER_STATUS = [('paid', 'The financial have been paid'),
                ('unpaid', 'The financial have not been paid'),
                ('any', 'Order financial is any')]


class WooCommerceOrderWorkflowConfig(models.Model):
    _name = 'woo.order.workflow.config'
    _description = "WooCommerce Order Workflow Configuration"

    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms', default=lambda self: self.env.ref('account.account_payment_term_immediate', raise_if_not_found=False))
    order_workflow_id = fields.Many2one("order.workflow.config.ts", "Marketplace Workflow")
    payment_gateway_id = fields.Many2one("woo.payment.gateway.ts", "Payment Gateway")
    order_status = fields.Selection(ORDER_STATUS, help="WooCommerce Order's Financial Status.", default='any')

    @api.constrains('mk_instance_id', 'payment_gateway_id', 'order_status')
    def _check_unique_financial_workflow(self):
        for workflow in self:
            domain = [('id', '!=', workflow.id), ('mk_instance_id', '=', workflow.mk_instance_id.id), ('payment_gateway_id', '=', workflow.payment_gateway_id.id),
                      ('order_status', '=', workflow.order_status)]
            if self.search(domain):
                raise ValidationError(_('You cannot create duplicate Order Workflow Configuration!'))
