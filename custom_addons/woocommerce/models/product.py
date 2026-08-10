from odoo import models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def woocommerce_prepare_vals_for_update_listing(self, mk_instance_id):
        listing_vals = {}
        woo_categ_obj = self.env['woo.product.category.ts']
        if self.categ_id:
            woo_categ_id = woo_categ_obj.get_woocommerce_category(self.categ_id, mk_instance_id)
            listing_vals.update({'woo_categ_ids': [(6, 0, woo_categ_id.ids)]})
        return listing_vals

    def woocommerce_prepare_vals_for_create_listing(self, mk_instance_id):
        listing_vals = self.woocommerce_prepare_vals_for_update_listing(mk_instance_id)
        return listing_vals
