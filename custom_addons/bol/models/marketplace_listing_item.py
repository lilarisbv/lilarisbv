import logging

from odoo import models, fields, tools
from odoo.tools import float_is_zero

_logger = logging.getLogger("TeqStars:bol")


class MkListingItem(models.Model):
    _inherit = "mk.listing.item"

    def bol_hide_fields(self):
        return ['item_create_date', 'item_update_date', 'sequence', 'is_taxable']

    def bol_hide_page(self):
        return ['product_images', 'other_info']

    def bol_action_change_price_view(self):
        return self.env.ref('bol.mk_product_pricelist_item_advanced_bol_tree_view')

    def _get_applicable_pricelist_quantities(self, pricelist, product, at_date, _seen=None):
        if _seen is None:
            _seen = set()
        if not pricelist or pricelist.id in _seen:
            return set()
        _seen.add(pricelist.id)
        rules = pricelist._get_applicable_rules(product, at_date)
        matching_rules = rules.filtered(lambda r: r._is_applicable_for(product, r.min_quantity or 1.0))
        quantities = {r.min_quantity or 1.0 for r in matching_rules}
        quantities.add(1.0)
        for rule in matching_rules.filtered(lambda r: r.base == 'pricelist' and r.base_pricelist_id):
            quantities |= self._get_applicable_pricelist_quantities(rule.base_pricelist_id, product, at_date, _seen=_seen)
        return quantities

    def get_offer_price_for_bol(self):
        self.ensure_one()
        at_date = fields.Datetime.now()
        product = self.product_id
        pricelist = self.mk_instance_id.pricelist_id
        price_unit_prec = self.env['decimal.precision'].precision_get('Product Price')
        quantities = self._get_applicable_pricelist_quantities(pricelist, product, at_date)
        bundle_prices = {}
        for qty in sorted(quantities):
            price = pricelist._get_product_price(product, qty, uom=product.uom_id, date=at_date)
            int_qty = int(qty)
            if not 1 <= price <= 9999:
                if not float_is_zero(price, precision_digits=price_unit_prec):
                    _logger.warning("Bol.com price tier skipped for %s: quantity %s, price %s is outside the accepted range of 1 to 9999.", product.display_name, int_qty, price)
                continue
            if int_qty not in bundle_prices:
                bundle_prices[int_qty] = price
        return {'bundlePrices': [{'quantity': qty, 'unitPrice': price} for qty, price in sorted(bundle_prices.items())]}

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
                pricelist_item_id.sudo().write({'compute_price': 'fixed', 'fixed_price': bol_price})
                pricelist_items_to_remove -= pricelist_item_id
            elif not pricelist_item_id:
                instance_id.pricelist_id.sudo().write({'item_ids': [(0, 0, {
                    'applied_on': '0_product_variant',
                    'product_id': self.product_id.id,
                    'product_tmpl_id': self.product_id.product_tmpl_id.id,
                    'compute_price': 'fixed',
                    'min_quantity': quantity,
                    'fixed_price': bol_price
                })]})
        if update_product_price and pricelist_items_to_remove:
            pricelist_items_to_remove.sudo().unlink()
        return True
