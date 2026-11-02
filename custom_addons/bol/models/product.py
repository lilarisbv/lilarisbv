from odoo import models
from odoo.exceptions import ValidationError
from .misc import _normalize_barcode_for_bol, _get_possible_barcodes


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def bol_prepare_vals_for_create_listing(self, mk_instance_id):
        self.ensure_one()
        fulfilment_method = mk_instance_id.bol_operation_type
        if fulfilment_method == 'Both':
            fulfilment_method = self._context.get('bol_fulfilment_method', 'FBR')
        vals = {
            'bol_condition_category': mk_instance_id.bol_default_condition_category or 'NEW',
            'bol_fulfilment_method': fulfilment_method,
            'bol_fulfilment_schedule': mk_instance_id.bol_default_fulfilment_schedule or 'BOL_DELIVERY_PROMISE',
            # bol_default_delivery_min_days legitimately allows 0 (e.g. the 24h delivery promise), so an
            # `or 2` fallback (or an `in (False, None)` check - 0 == False in Python) would silently
            # overwrite a real 0. `is` identity checks are required to tell 0 apart from False/None here.
            'bol_delivery_min_days': mk_instance_id.bol_default_delivery_min_days if mk_instance_id.bol_default_delivery_min_days is not False and mk_instance_id.bol_default_delivery_min_days is not None else 2,
            'bol_delivery_max_days': mk_instance_id.bol_default_delivery_max_days if mk_instance_id.bol_default_delivery_max_days is not False and mk_instance_id.bol_default_delivery_max_days is not None else 3,
        }
        category = vals['bol_condition_category']
        if category == 'NEW':
            vals['bol_condition'] = 'NEW'
        elif category == 'SECONDHAND':
            if not mk_instance_id.bol_default_condition_state:
                raise ValidationError("Condition State is required for SECONDHAND items.")
            # bol_condition must mirror the condition *state*, not the literal 'SECONDHAND' category
            # string (see _bol_parse_condition_response's equivalent branch, and Issue 25's regression test).
            vals['bol_condition_state'] = mk_instance_id.bol_default_condition_state
            vals['bol_condition'] = mk_instance_id.bol_default_condition_state
        elif category == 'REFURBISHED':
            if not mk_instance_id.bol_default_condition_grade:
                raise ValidationError("Refurbished Grade is required for REFURBISHED items.")
            vals['bol_condition_grade'] = mk_instance_id.bol_default_condition_grade
            vals['bol_condition_margin'] = mk_instance_id.bol_default_condition_margin
            # Legacy v10 condition mirrors the refurbished grade, same mapping as _bol_parse_condition_response.
            vals['bol_condition'] = {'A': 'AS_NEW', 'B': 'GOOD', 'C': 'MODERATE'}.get(mk_instance_id.bol_default_condition_grade, 'AS_NEW')
        if vals['bol_fulfilment_schedule'] == 'BOL_DELIVERY_PROMISE':
            min_days = vals['bol_delivery_min_days']
            max_days = vals['bol_delivery_max_days']
            if min_days is None or max_days is None:
                raise ValidationError("Delivery min/max days are required.")
            vals['bol_delivery_code'] = f"{min_days}-{max_days}d"
            if min_days == 0 and max_days == 1:
                if not mk_instance_id.bol_default_ultimate_order_time:
                    raise ValidationError("Ultimate Order Time is required for 24h delivery.")
                vals['bol_ultimate_order_time'] = mk_instance_id.bol_default_ultimate_order_time
        return vals

    def create_or_update_listing(self, instance):
        if not instance.marketplace == 'bol':
            return super(ProductTemplate, self).create_or_update_listing(instance)
        self.ensure_one()
        listing_obj = self.env['mk.listing']
        listing_item_obj = self.env['mk.listing.item']
        created_or_updated_listing_ids = self.env['mk.listing']
        for variant_id in self.product_variant_ids:
            listing_item_id = listing_item_obj
            for candidate in _get_possible_barcodes(variant_id.barcode):
                listing_item_id = listing_item_obj.search([('mk_instance_id', '=', instance.id), ('barcode', '=', candidate), ('product_id', '=', variant_id.id)], limit=1)
                if listing_item_id:
                    break
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
        return {'barcode': _normalize_barcode_for_bol(self.barcode) or self.barcode}

    def bol_prepare_vals_for_update_listing_item(self, mk_instance_id):
        return {'barcode': _normalize_barcode_for_bol(self.barcode) or self.barcode}

    def create_or_update_listing_image(self, listing_item_id):
        if listing_item_id and listing_item_id.marketplace == 'bol':
            return True
        return super(ProductProduct, self).create_or_update_listing_image(listing_item_id)
