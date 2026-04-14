from odoo import models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def bol_prepare_vals_for_create_listing(self, mk_instance_id):
        vals = {'bol_condition': 'NEW', 'bol_fulfilment_method': mk_instance_id.bol_operation_type, 'bol_delivery_code': '2-3d'}
        if mk_instance_id.bol_operation_type == 'Both':
            vals.update({'bol_fulfilment_method': self.env.context.get('bol_fulfilment_method', 'FBR')})
        return vals

    def create_or_update_listing(self, instance):
        if not instance.marketplace == 'bol':
            return super(ProductTemplate, self).create_or_update_listing(instance)
        self.ensure_one()
        listing_obj = self.env['mk.listing']
        listing_item_obj = self.env['mk.listing.item']
        created_or_updated_listing_ids = self.env['mk.listing']
        for variant_id in self.product_variant_ids:
            listing_item_id = listing_item_obj.search([('mk_instance_id', '=', instance.id), ('barcode', '=', variant_id.barcode), ('product_id', '=', variant_id.id)])
            listing_id = listing_item_id.mk_listing_id
            if not listing_item_id:
                vals = self.prepare_vals_for_create_listing(instance)
                listing_id = listing_obj.create(vals)
            else:
                vals = self.prepare_vals_for_update_listing(instance)
                listing_id.write(vals)
            listing_item_id = variant_id.create_or_update_listing_item(instance, 1, listing_id)
            listing_id.write({'name': listing_item_id.name})
            created_or_updated_listing_ids |= listing_id
        return created_or_updated_listing_ids


class ProductProduct(models.Model):
    _inherit = "product.product"

    def bol_prepare_vals_for_create_listing_item(self, mk_instance_id):
        return {'barcode': self.barcode}

    def bol_prepare_vals_for_update_listing_item(self, mk_instance_id):
        return {'barcode': self.barcode}

    def create_or_update_listing_image(self, listing_item_id):
        if listing_item_id and listing_item_id.marketplace == 'bol':
            return True
        return super(ProductProduct, self).create_or_update_listing_image(listing_item_id)
