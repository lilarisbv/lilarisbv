from odoo import fields, models

class WooCommerceProductAttribute(models.Model):
    _name = "woo.product.attribute"
    _description = "WooCommerce Product Attribute"

    name = fields.Char("Name", required=True)
    slug = fields.Char("Code", copy=False)
    mk_id = fields.Char("Marketplace Identification", copy=False)
    exported_in_woocommerce = fields.Boolean("Exported in WooCommerce", default=False, copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", copy=False, ondelete='cascade', required=True)
    attribute_id = fields.Many2one('product.attribute', string="Attribute", ondelete='cascade', required=True)

    def prepare_vals_for_update_odoo_woocommerce_attribute(self, attribute_dict, attribute_id, mk_instance_id):
        attribute_vals = {'name': attribute_dict.get('name'),
                          'slug': attribute_dict.get('slug'),
                          'mk_id': attribute_dict.get('id'),
                          'exported_in_woocommerce': True,
                          'attribute_id': attribute_id.id,
                          'mk_instance_id': mk_instance_id.id}
        return attribute_vals

    def get_odoo_attribute_for_woocommerce(self, name):
        odoo_attribute_obj = self.env['product.attribute']
        odoo_attribute_id = odoo_attribute_obj.search([('name', '=ilike', name)], limit=1)
        if not odoo_attribute_id:
            odoo_attribute_id = odoo_attribute_obj.create(({'name': name, 'display_type': 'radio', 'create_variant': 'always'}))
        return odoo_attribute_id

    def import_product_attribute_from_woocommerce(self, mk_instance_id):
        attribute_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'products/attributes')
        if status_code not in [200, 201]:
            return False
        for attribute_dict in attribute_list:
            name = attribute_dict.get('name')
            odoo_attribute_id = self.get_odoo_attribute_for_woocommerce(name)
            attribute_id = self.search([('name', '=', name), ('attribute_id', '=', odoo_attribute_id.id), ('mk_instance_id', '=', mk_instance_id.id)])
            attribute_vals = self.prepare_vals_for_update_odoo_woocommerce_attribute(attribute_dict, odoo_attribute_id, mk_instance_id)
            if not attribute_id:
                self.create(attribute_vals)
            else:
                attribute_id.write(attribute_vals)
        return True

    def find_create_locally_attribute(self, mk_instance_id, product_attribute):
        woo_attribute_id = self.env['product.attribute']
        if product_attribute:
            woo_attribute_obj = self.env['woo.product.attribute']
            woo_attribute_id = woo_attribute_obj.search(
                [('attribute_id', '=', product_attribute.id), ('mk_instance_id', '=', mk_instance_id.id)])
            if not woo_attribute_id:
                vals = {'name': product_attribute.name,
                        'slug': product_attribute.name,
                        'exported_in_woocommerce': False,
                        'attribute_id': product_attribute.id,
                        'mk_instance_id': mk_instance_id.id}
                woo_attribute_id = woo_attribute_obj.create(vals)
        return woo_attribute_id

    def export_attribute_odoo_to_woocommerce(self, mk_instance_id, product_attribute=[]):
        """Note Just for knowledge : Attribute export odoo to woo to show in Products > Attributes in woo : The attribute values automatically appending in the term of woo
        e.g. We have defined different values which is not available in the woo it will automatically added just we need append in option when we exporting or updating product."""
        woo_attribute_obj = self.env['woo.product.attribute']
        product_attribute = product_attribute or self.env['product.attribute']
        woo_att_ids = woo_attribute_obj
        for attr in product_attribute:
            woo_attribute_id = self.find_create_locally_attribute(mk_instance_id, product_attribute=attr)
            woo_att_ids += woo_attribute_id
            if not woo_attribute_id.exported_in_woocommerce:
                vals = {'name': product_attribute.name, 'slug': product_attribute.name}
                res = mk_instance_id.fetch_data_from_woocommerce('post', 'products/attributes', data=vals)
                if res[1] not in [200, 201]:
                    return woo_attribute_obj
                else:
                    res = res[0][0]
                woo_attribute_id.write({
                    'mk_id': res.get('id'),
                    'slug': res.get('slug'),
                    'exported_in_woocommerce': True,
                    # 'type': res.get('type'),
                    # 'order_by': res.get('order_by'),
                    # 'has_archives': res.get('has_archives'),
                })
                self.env.cr.commit()
        return woo_att_ids

    def get_product_attribute_from_woocommerce(self, mk_instance_id, product_attribute=False):
        woo_attribute_obj = self.env['woo.product.attribute']
        woo_attribute_id = woo_attribute_obj
        self.import_product_attribute_from_woocommerce(mk_instance_id)
        if product_attribute:
            woo_attribute_id = woo_attribute_obj.search(
                [('attribute_id', '=', product_attribute.id), ('mk_instance_id', '=', mk_instance_id.id), ('exported_in_woocommerce', '=', True)],limit=1)
            if not woo_attribute_id:
                woo_attribute_id = self.export_attribute_odoo_to_woocommerce(mk_instance_id, product_attribute)
        return woo_attribute_id
