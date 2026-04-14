from odoo import models, fields, tools
from odoo.tools import float_is_zero


class MkListingItem(models.Model):
    _inherit = "mk.listing.item"

    def bol_hide_fields(self):
        return ['item_create_date', 'item_update_date', 'sequence', 'is_taxable']

    def bol_hide_page(self):
        return ['product_images', 'other_info']

    def bol_action_change_price_view(self):
        return self.env.ref('bol.mk_product_pricelist_item_advanced_bol_tree_view')

    def get_offer_price_for_bol(self):
        self.ensure_one()
        # if not self.mk_id:
        #     return False
        date = fields.Datetime.now()
        bundle_prices = {'bundlePrices': []}
        product = self.product_id
        mk_instance_id = self.mk_instance_id
        price_unit_prec = self.env['decimal.precision'].precision_get('Product Price')
        items = mk_instance_id.pricelist_id.item_ids.filtered(lambda x: x.product_id == self.product_id and (not x.date_start or x.date_start <= date) and (not x.date_end or x.date_end >= date))
        for item in items:
            price = mk_instance_id.pricelist_id._get_product_price(product, item.min_quantity or 1.0, uom=product.uom_id)
            if not float_is_zero(price, precision_digits=price_unit_prec):
                bundle_prices['bundlePrices'].append({'quantity': int(item.min_quantity or 1.0), 'unitPrice': price})
        return bundle_prices

    def create_or_update_pricelist_item(self, variant_price, update_product_price=False, reversal_convert=False, skip_conversion=False):
        if self.marketplace != 'bol':
            return super(MkListingItem, self).create_or_update_pricelist_item(variant_price, update_product_price, reversal_convert, skip_conversion)
        res = self.product_id.product_tmpl_id.taxes_id.compute_all(variant_price, product=self.product_id.product_tmpl_id, partner=self.env['res.partner'])
        tax_included_price = tools.float_round(res['total_included'], precision_digits=self.env['decimal.precision'].precision_get('Product Price'))
        return super(MkListingItem, self).create_or_update_pricelist_item(tax_included_price, update_product_price, reversal_convert, skip_conversion)

    def create_or_update_pricelist_item_for_bol(self, bol_pricing, update_product_price=False):
        self.ensure_one()
        instance_id = self.mk_instance_id or self.mk_listing_id.mk_instance_id
        bundle_prices = bol_pricing.get('bundlePrices')
        pricelist_items_to_remove = self.env['product.pricelist.item'].search([('pricelist_id', '=', instance_id.pricelist_id.id), ('product_id', '=', self.product_id.id)])
        for price_dict in bundle_prices:
            quantity, bol_price = price_dict.get('quantity'), price_dict.get('unitPrice')
            pricelist_item_id = pricelist_items_to_remove.filtered(lambda x: x.min_quantity == quantity)
            if pricelist_item_id and update_product_price:
                pricelist_item_id.write({'compute_price': 'fixed', 'fixed_price': bol_price})
                pricelist_items_to_remove -= pricelist_item_id
            else:
                instance_id.pricelist_id.write({'item_ids': [(0, 0, {
                    'applied_on': '0_product_variant',
                    'product_id': self.product_id.id,
                    'product_tmpl_id': self.product_id.product_tmpl_id.id,
                    'compute_price': 'fixed',
                    'min_quantity': quantity,
                    'fixed_price': bol_price
                })]})
        if pricelist_items_to_remove:
            pricelist_items_to_remove.unlink()
        return True
