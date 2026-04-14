from odoo import fields, models


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    bol_transporter_id = fields.Many2one('bol.transporter.code', string="Bol's Transporter")

    def bol_search_create_delivery_carrier(self, transporter_code, mk_instance_id):
        carrier = False
        if transporter_code:
            carrier = self.search([('bol_transporter_id.code', '=', transporter_code)], limit=1)
            if not carrier:
                carrier = self.search(['|', ('name', '=', transporter_code), ('bol_transporter_id.code', '=', transporter_code)], limit=1)
            if not carrier:
                delivery_product = mk_instance_id.delivery_product_id
                bol_transporter_id = self.env['bol.transporter.code'].search([('code', '=', transporter_code)], limit=1)
                if not bol_transporter_id:
                    bol_transporter_id = self.env['bol.transporter.code'].create({'name': transporter_code, 'code': transporter_code})
                if not delivery_product:
                    delivery_template = self.env['product.template'].create({'name': transporter_code, 'type': 'service'})
                    delivery_product = delivery_template.product_variant_ids
                carrier = self.create({'name': transporter_code, 'bol_transporter_id': bol_transporter_id.id, 'product_id': delivery_product.id})
        return carrier
