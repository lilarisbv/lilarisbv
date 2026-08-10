from odoo import models, fields, api, _
from odoo.addons.woocommerce.models.order_workflow_config import ORDER_STATUS


class MKLogLine(models.Model):
    _inherit = "mk.log.line"

    woocommerce_payment_gateway_id = fields.Many2one("woo.payment.gateway.ts", "Payment Gateway")
    order_status = fields.Selection(ORDER_STATUS, help="WooCommerce Order's Financial Status.")

    def do_configure_woocommerce_order_workflow(self):
        workflow_id = self.env['woo.order.workflow.config'].search([('mk_instance_id', '=', self.mk_instance_id.id), ('payment_gateway_id', '=', self.woocommerce_payment_gateway_id.id), ('order_status', '=', self.order_status)])
        if workflow_id:
            self.woocommerce_payment_gateway_id = False
            self.order_status = ''
            return True
        return {
            'name': _('Order Workflow Configuration'),
            'res_model': 'woo.order.workflow.config',
            'view_mode': 'form',
            'context': {
                'default_mk_instance_id': self.mk_instance_id.id,
                'default_payment_gateway_id': self.woocommerce_payment_gateway_id.id,
                'default_order_status': self.order_status,
            },
            'target': 'new',
            'type': 'ir.actions.act_window',
        }
