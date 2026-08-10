from odoo import models, fields, _
from odoo.exceptions import AccessError


class WooCommercePaymentGateway(models.Model):
    _name = 'woo.payment.gateway.ts'
    _description = "WooCommerce Payment Gateway"

    name = fields.Char("Name", required=True)
    code = fields.Char("Code", copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", copy=False, ondelete='cascade')

    _unique_woo_payment_gateway_id = models.Constraint(
        "unique (code, mk_instance_id)", "A WooCommerce payment gateway with the same code can be imported only once !"
    )

    def set_woo_payment_gateways(self, mk_instance_id):
        payment_gateways_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'payment_gateways')
        if status_code not in [200, 201]:
            raise AccessError(_("WooCommerce: Error while fetching Payment Gateways. ERROR: {}".format(payment_gateways_list)))
        for payment_gateway in payment_gateways_list:
            if payment_gateway.get('enabled', False):
                name = payment_gateway.get('method_title')
                code = payment_gateway.get('id')
                payment_id = self.search([('code', '=', code), ('mk_instance_id', '=', mk_instance_id.id)])
                if not payment_id:
                    self.create({'code': code, 'name': name, 'mk_instance_id': mk_instance_id.id})
        return True
