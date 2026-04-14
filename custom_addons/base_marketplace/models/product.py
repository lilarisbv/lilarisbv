import hashlib

from odoo import models, fields, _
from odoo.exceptions import RedirectWarning


class ProductTemplate(models.Model):
    _inherit = "product.template"

    mk_listing_ids = fields.One2many('mk.listing', 'product_tmpl_id', string="Listing")

    def prepare_vals_for_create_listing(self, instance):
        self.ensure_one()
        vals = {
            'name': self.name,
            'mk_instance_id': instance.id,
            'product_tmpl_id': self.id,
            'description': self.description_sale,
            'product_category_id': self.categ_id.id
        }
        if hasattr(self, '%s_prepare_vals_for_create_listing' % instance.marketplace):
            vals.update(getattr(self, '%s_prepare_vals_for_create_listing' % instance.marketplace)(instance))
        return vals

    def prepare_vals_for_update_listing(self, instance):
        self.ensure_one()
        vals = {
            'name': self.name,
            'product_category_id': self.categ_id.id,
        }
        if hasattr(self, '%s_prepare_vals_for_update_listing' % instance.marketplace):
            vals.update(getattr(self, '%s_prepare_vals_for_update_listing' % instance.marketplace)(instance))
        return vals

    def create_or_update_listing_image(self, listing_id):
        if not self.image_1920:
            return True
        listing_image_obj = self.env['mk.listing.image']
        image_hex = hashlib.md5(self.image_1920).hexdigest()
        listing_image_id = listing_image_obj.search([('image_hex', '=', image_hex), ('mk_listing_id', '=', listing_id.id)], limit=1)
        if listing_image_id:
            return True
        listing_image_obj.create({'name': listing_id.name, 'image': self.image_1920, 'mk_listing_id': listing_id.id, })
        return True

    def create_or_update_listing(self, instance):
        """
        Task: T7309 - Refactor Listing Image Handling to Marketplace
        Calls image handling method to add listing images if product images are available.
        """
        self.ensure_one()
        listing_obj = self.env['mk.listing']
        listing_id = listing_obj.search([('mk_instance_id', '=', instance.id), ('product_tmpl_id', '=', self.id)])
        if not listing_id:
            vals = self.prepare_vals_for_create_listing(instance)
            listing_id = listing_obj.create(vals)
        else:
            vals = self.prepare_vals_for_update_listing(instance)
            listing_id.write(vals)
        # self.create_or_update_listing_image(listing_id)
        sequence = 1
        possible_combinations = self._get_possible_combinations()
        combinations = [combination for combination in possible_combinations]
        for combination in combinations:
            product_variant_id = self._get_variant_for_combination(combination)
            product_variant_id.create_or_update_listing_item(instance, sequence, listing_id)
            sequence += 1

        # Ensure product_template_image_ids attribute exists before syncing images
        if hasattr(self, 'product_template_image_ids'):
            self.handle_listing_images(listing_id)

        return listing_id

    def toggle_active(self):
        res = super(ProductTemplate, self).toggle_active()
        for product_id in self:
            if product_id.active == False and product_id.mk_listing_ids:
                product_id.mk_listing_ids.unlink()
        return res

    def write(self, vals):
        res = super(ProductTemplate, self).write(vals)
        if 'list_price' in vals:
            for listing_item_id in self.mapped('mk_listing_ids').mapped('listing_item_ids'):
                mk_instance_id = listing_item_id.mk_instance_id
                variant_price = vals.get('list_price')
                if mk_instance_id.is_export_product_sale_price and variant_price > 0:
                    listing_item_id.create_or_update_pricelist_item(variant_price, update_product_price=True, reversal_convert=True)
        return res

    def _check_barcode_and_sku(self, mk_instance_id):
        """
        Check barcode and sku set in product variant based on the configuration of barcode or sku.
        Args:
          self: The product template to check.
          mk_instance_id: instance to get configuration
        Returns:
          True if the barcode and sku are set and match the configuration, False otherwise.
        """
        self.ensure_one()
        is_validate = True
        sync_product_with = mk_instance_id.sync_product_with
        for product_variant_id in self.product_variant_ids:
            barcode = product_variant_id.barcode
            sku = product_variant_id.default_code

            # Check if barcode and sku are set.
            if not barcode and not sku:
                is_validate = False
                break

            # Check if barcode or sku match the configuration.
            if sync_product_with == 'barcode_or_sku' and not sku and not barcode:
                is_validate = False
                break

            elif sync_product_with == 'sku' and not sku:
                is_validate = False
                break

            elif sync_product_with == 'barcode' and not barcode:
                is_validate = False
                break
        return is_validate

    def handle_listing_images(self, listing_id):
        """
        Task: T7309 - Refactor Listing Image Handling to Marketplace
        This hook type method is used to handle marketplace-specific listing image handling.
        Args:
            listing_id (recordset): Recordset of mk.listing model.
        Returns:
            Returns True if no specific handler is found, otherwise returns the result of the called marketplace-specific method.
        """
        marketplace = listing_id.mk_instance_id.marketplace
        if hasattr(self, 'handle_%s_listing_images' % marketplace):
            return getattr(self, 'handle_%s_listing_images' % marketplace)(listing_id)
        return True


class ProductProduct(models.Model):
    _inherit = "product.product"

    mk_listing_item_ids = fields.One2many('mk.listing.item', 'product_id', string="Listing Items")

    def prepare_vals_for_create_listing_item(self, instance, sequence, listing_id):
        self.ensure_one()
        vals = {
            'name': self.with_context(display_default_code=False).display_name,
            'sequence': sequence,
            'mk_instance_id': instance.id,
            'product_id': self.id,
            'default_code': self.default_code,
            'mk_listing_id': listing_id.id
        }
        if hasattr(self, '%s_prepare_vals_for_create_listing_item' % instance.marketplace):
            vals.update(getattr(self, '%s_prepare_vals_for_create_listing_item' % instance.marketplace)(instance))
        return vals

    def prepare_vals_for_update_listing_item(self, instance, sequence, listing_id):
        self.ensure_one()
        vals = {
            'sequence': sequence,
            'default_code': self.default_code,
        }
        if hasattr(self, '%s_prepare_vals_for_update_listing_item' % instance.marketplace):
            vals.update(getattr(self, '%s_prepare_vals_for_update_listing_item' % instance.marketplace)(instance))
        return vals

    def create_or_update_listing_image(self, listing_item_id):
        if not self.image_1920:
            return True
        listing_image_obj = self.env['mk.listing.image']
        image_hex = hashlib.md5(self.image_1920).hexdigest()
        listing_image_id = listing_image_obj.search([('image_hex', '=', image_hex), ('mk_listing_item_ids', 'in', listing_item_id.ids)], limit=1)
        if listing_image_id:
            return True
        listing_image_obj.create(
            {'name': listing_item_id.name, 'image': self.image_1920, 'mk_listing_id': listing_item_id.mk_listing_id.id, 'mk_listing_item_ids': [(6, 0, listing_item_id.ids)]})
        return True

    def create_or_update_listing_item(self, instance, sequence, listing_id):
        self.ensure_one()
        listing_item_obj = self.env['mk.listing.item']
        listing_item_id = listing_item_obj.search([('mk_instance_id', '=', instance.id), ('product_id', '=', self.id)])
        if not listing_item_id:
            vals = self.prepare_vals_for_create_listing_item(instance, sequence, listing_id)
            listing_item_id = listing_item_obj.create(vals)
            listing_item_id.create_or_update_pricelist_item(float(self.lst_price), update_product_price=False, reversal_convert=True)
        else:
            vals = self.prepare_vals_for_update_listing_item(instance, sequence, listing_id)
            listing_item_id.write(vals)
            pricelist_item_id = self.env['product.pricelist.item'].search([('pricelist_id', '=', instance.pricelist_id.id), ('product_id', '=', listing_item_id.product_id.id)],
                                                                          limit=1)
            if not pricelist_item_id:
                listing_item_id.create_or_update_pricelist_item(float(self.lst_price), reversal_convert=True)
        self.create_or_update_listing_image(listing_item_id)
        return listing_item_id

    def get_product_stock(self, export_qty_type, export_qty_value, location_id, stock_type):
        product_id = self.with_context(location=location_id.ids)
        stock = getattr(product_id, stock_type)
        if stock > 0:
            if export_qty_type == 'percentage':
                quantity = (stock * export_qty_value) / 100
                if quantity >= stock:
                    return stock
                else:
                    return quantity
            elif export_qty_type == 'fix':
                if export_qty_value >= stock:
                    return stock
                else:
                    return export_qty_value
        return stock

    def toggle_active(self):
        res = super(ProductProduct, self).toggle_active()
        for product_id in self:
            if product_id.active == False and product_id.mk_listing_item_ids:
                product_id.mk_listing_item_ids.unlink()
        return res

    def write(self, vals):
        if vals.get('active', '') == False:
            self._validate_variant_archive()
        res = super(ProductProduct, self).write(vals)
        if 'list_price' in vals:
            for listing_item_id in self.mapped('mk_listing_item_ids'):
                mk_instance_id = listing_item_id.mk_instance_id
                variant_price = vals.get('list_price')
                if mk_instance_id.is_export_product_sale_price and variant_price > 0:
                    listing_item_id.create_or_update_pricelist_item(variant_price, update_product_price=True, reversal_convert=True)
        return res

    def _validate_variant_archive(self):
        for variant_id in self:
            listing_item_ids = self.env['mk.listing.item'].search([('product_id', '=', variant_id.id)])
            if listing_item_ids:
                listings = listing_item_ids.mapped('mk_listing_id')
                listing_form_view = self.env.ref('base_marketplace.mk_listing_form_view')
                listing_tree_view = self.env.ref('base_marketplace.mk_listing_tree_view')

                action_data = {
                    'view_mode': 'list',
                    'name': _('Listing'),
                    'res_model': 'mk.listing',
                    'type': 'ir.actions.act_window',
                    'domain': [('id', 'in', listings.ids)],
                    'views': [[listing_tree_view.id, 'list'], [listing_form_view.id, 'form']],
                    'target': 'current',
                }
                if len(listings) == 1:
                    action_data['views'] = [(listing_form_view.id, 'form')]
                    action_data['res_id'] = listings.id

                error_msg = _(f'You cannot archive a product that is already linked to a listing. Please delete the below listing(s) first in order to archive the product.\n')
                error_msg += ''.join(f'    - [{listing.mk_instance_id.name}] {listing.name}\n' for listing in listings)
                raise RedirectWarning(error_msg, action_data, "Open Listing(s)")


class ProductTemplateAttributeLine(models.Model):
    _inherit = 'product.template.attribute.line'

    def create_or_update_ptal(self, attribute_dict, product_tmpl_id):
        for name, value in attribute_dict.items():
            ptal_id = self.env['product.template.attribute.line'].search([('product_tmpl_id', '=', product_tmpl_id.id), ('attribute_id.name', '=ilike', name)])
            if not ptal_id:
                # product_tmpl_id.write({'attribute_line_ids': attribute_line_vals})
                # ptal_id = self.search([('product_tmpl_id', '=', product_tmpl_id.id), ('attribute_id.name', '=ilike', name)])
                return False
            attribute_value_id = self.env['product.attribute.value'].search([('name', '=ilike', value), ('attribute_id', '=', ptal_id.attribute_id.id)], limit=1)
            if not attribute_value_id:
                attribute_value_id = self.env['product.attribute.value'].create({'name': value, 'attribute_id': ptal_id.attribute_id.id})
            if attribute_value_id not in ptal_id.value_ids:
                ptal_id.write({'value_ids': [(4, attribute_value_id.id, False)]})
        return True
