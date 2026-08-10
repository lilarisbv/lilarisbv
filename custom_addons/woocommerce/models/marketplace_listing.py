import base64
import pprint
import requests
from itertools import groupby
from datetime import datetime
from odoo.tools.misc import split_every
from odoo import models, fields, tools, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DF
from odoo.tools import float_is_zero
from odoo.addons.woocommerce.models.misc import convert_woocommerce_datetime_to_utc


class MkListing(models.Model):
    _inherit = "mk.listing"

    woo_categ_ids = fields.Many2many('woo.product.category.ts', 'woo_categ_listing_rel', 'listing_id', 'categ_id', string='Category')
    woo_tag_ids = fields.Many2many('woo.product.tags.ts', 'woo_tag_listing_rel', 'listing_id', 'tag_id', string='Tags')
    slug = fields.Char('Code', copy=False)
    short_description = fields.Html('Short Description', sanitize_attributes=False)

    def woocommerce_hide_fields(self):
        return ['listing_publish_date', 'product_category_id']

    def woocommerce_hide_page(self):
        return []

    def woocommerce_export_product_limit(self):
        """
        Checking for maximum product export limit to prevent user's process.

        :return: True if selected product isn't more than limit.
        :rtype: bool
        :raise UserError:
                * if selected product more than given limit.
        """
        max_limit = 80
        if self and len(self) > max_limit:
            raise UserError(_("System will not permits to send out more then 80 items all at once. Please select just 80 items for export."))
        return True

    def publish_product_in_woocommerce(self):
        mk_instance_id = self.mk_instance_id
        if self.mk_id:
            try:
                response, status = mk_instance_id.fetch_data_from_woocommerce('put', "products/{}".format(self.mk_id), data={'status': 'publish'})
                if status in [200, 201]:
                    result_dict = response[0]
                    updated_at = convert_woocommerce_datetime_to_utc(result_dict.get("date_modified", ""))
                    self.write({'is_published': True,
                                'listing_update_date': updated_at})
                else:
                    raise ValidationError(_("Error while publish product. ERROR: {}".format(response.content)))
            except Exception as e:
                if not self.mk_id:
                    log_message = "WooCommerce Product {} is not found in WooCommerce while trying to Publish.".format(self.mk_id)
                    raise ValidationError(_(log_message))
                else:
                    log_message = e and e.obj and str(e.obj) or e
                    raise ValidationError(_(log_message))
        return True

    def unpublish_product_in_woocommerce(self):
        mk_instance_id = self.mk_instance_id
        if self.mk_id:
            try:
                response, status = mk_instance_id.fetch_data_from_woocommerce('put', "products/{}".format(self.mk_id), data={'status': 'draft'})
                if status in [200, 201]:
                    result_dict = response[0]
                    updated_at = convert_woocommerce_datetime_to_utc(result_dict.get("date_modified", ""))
                    self.write({'is_published': False,
                                'listing_update_date': updated_at})
                else:
                    raise ValidationError(_("Error while unpublish product. ERROR: {}".format(response.content)))
            except Exception as e:
                if not self.mk_id:
                    log_message = "WooCommerce Product {} is not found in WooCommerce while trying to Publish.".format(self.mk_id)
                    raise ValidationError(_(log_message))
                else:
                    log_message = e and e.obj and str(e.obj) or e
                    raise ValidationError(_(log_message))
        return True

    def woocommerce_published(self):
        if not self.is_published:
            self.publish_product_in_woocommerce()
        else:
            self.unpublish_product_in_woocommerce()
        return True

    def woocommerce_open_export_listing_view(self):
        action = self.env.ref('base_marketplace.action_product_export_to_marketplace').read()[0]
        action['name'] = _("Export New Products to WooCommerce")
        action['views'] = [(self.env.ref('woocommerce.mk_operation_export_woocommerce_listing_to_mk_view').id, 'form')]
        action['context'] = self.env.context.copy()
        return action

    def woocommerce_open_update_listing_view(self):
        action = self.env.ref('base_marketplace.action_listing_update_to_marketplace').read()[0]
        action['name'] = _("Update Products in WooCommerce")
        action['views'] = [(self.env.ref('woocommerce.mk_operation_update_woocommerce_listing_to_mk_view').id, 'form')]
        action['context'] = self.env.context.copy()
        return action

    def get_woocommerce_existing_mk_listing_and_odoo_product(self, variant_list, mk_instance_id):
        existing_mk_product = {}
        existing_odoo_product = {}
        odoo_product_template = self.env['product.template']
        for variant_dict in variant_list:
            odoo_product_id, listing_item_id = self.get_odoo_product_variant_and_listing_item(mk_instance_id, variant_dict.get("id", ""), variant_dict.get("barcode", ""),
                                                                                              variant_dict.get("sku", ""))
            if odoo_product_id:
                odoo_product_template |= odoo_product_id.product_tmpl_id
                existing_odoo_product.update({variant_dict.get("id"): odoo_product_id})
            elif listing_item_id and not odoo_product_id:
                existing_odoo_product.update({variant_dict.get("id"): listing_item_id.product_id})
            listing_item_id and existing_mk_product.update({variant_dict.get("id"): listing_item_id})
        return existing_mk_product, existing_odoo_product, odoo_product_template

    def prepare_marketplace_listing_vals_for_woocommerce(self, mk_instance_id, product_dict, variant_list, odoo_product_id, product_category_ids, tag_ids):
        vals = {}
        product_published = True if product_dict.get('status', '') == 'publish' else False
        if product_category_ids:
            vals.update({'woo_categ_ids': [(6, 0, product_category_ids.ids)]})

        if tag_ids:
            vals.update({'woo_tag_ids': [(6, 0, tag_ids)]})

        vals.update(
            {'is_listed': True,
             'name': product_dict.get("name", ""),
             'mk_id': product_dict.get("id", ""),
             'slug': product_dict.get("slug", ""),
             'mk_instance_id': mk_instance_id.id,
             'product_tmpl_id': odoo_product_id.product_tmpl_id.id,
             'listing_create_date': convert_woocommerce_datetime_to_utc(product_dict.get("date_created", "")),
             'listing_update_date': convert_woocommerce_datetime_to_utc(product_dict.get("date_modified", "")),
             'description': product_dict.get("description", ""),
             'is_published': product_published,
             'short_description': product_dict.get("short_description", ""),
             'number_of_variants_in_mk': len(variant_list)})
        return vals

    def prepare_marketplace_listing_item_vals_for_woocommerce(self, product_dict, variant_dict, mk_instance_id, odoo_product_id, mk_listing_id):
        variant_title = variant_dict.get("name", "") or product_dict.get("name", "")
        vals = {
            'name': variant_title,
            'product_id': odoo_product_id.id,
            'default_code': variant_dict.get("sku", ""),
            'mk_listing_id': mk_listing_id.id,
            'mk_id': variant_dict.get("id", ""),
            'mk_instance_id': mk_instance_id.id,
            'item_create_date': convert_woocommerce_datetime_to_utc(variant_dict.get("date_created", "")),
            'item_update_date': convert_woocommerce_datetime_to_utc(variant_dict.get("date_modified", "")),
            'is_listed': True,
            'woo_tax_status': variant_dict.get("tax_status", "None"),
            'woo_tax_class': variant_dict.get("tax_class", ""),
            'woo_manage_stock': variant_dict.get('manage_stock', True),
        }
        return vals

    def _marketplace_convert_weight_woocommerce(self, weight, weight_name, mk_instance_id=False, reverse=False):
        mk_uom_id = False
        if weight_name == 'lb':
            mk_uom_id = self.env.ref('uom.product_uom_lb')
        elif weight_name == 'kg':
            mk_uom_id = self.env.ref('uom.product_uom_kgm')
        elif weight_name == 'oz':
            mk_uom_id = self.env.ref('uom.product_uom_oz')
        elif weight_name == 'g':
            mk_uom_id = self.env.ref('uom.product_uom_gram')
        elif weight_name == 't':
            mk_uom_id = self.env.ref('uom.product_uom_ton')
        if mk_instance_id:
            weight_uom_id = mk_instance_id.woo_weight_uom_id
        else:
            weight_uom_id = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
        if mk_uom_id:
            return weight_uom_id._compute_quantity(weight, mk_uom_id, round=False) if reverse else mk_uom_id._compute_quantity(weight, weight_uom_id, round=False)
        return False

    def create_update_woocommerce_product(self, product_dict, mk_instance_id, update_product_price=False, woo_update_image = False,is_update_existing_products=True):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        mk_listing_item_obj = self.env['mk.listing.item']
        mk_id = product_dict.get("id", "")
        variant_list = product_dict.get("variations", [])
        mk_listing_id = self.search([('mk_instance_id', '=', mk_instance_id.id), ('mk_id', '=', mk_id)])
        if mk_listing_id and not is_update_existing_products:
            return False

        if product_dict.get('grouped_products', False):
            log_message = "IMPORT LISTING: Skipped listing because Grouped product found: {} [{}]".format(product_dict.get('name', ''), mk_id)
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False

        if variant_list:
            variant_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'products/{}/variations'.format(mk_id))
            variant_list and product_dict.update({'variants': variant_list})
        else:
            product_dict.update({'variants': [product_dict]})
            variant_list = [product_dict]

        variant_sequence = 1
        listing_updated = False
        # Checking validation for marketplace product for duplicated SKU or Barcode before start importing.
        listing_item_validation_dict = {'name': product_dict.get('name'), 'id': product_dict.get('id'),
                                        'variants': [{'sku': variant_dict.get('sku'), 'id': variant_dict.get('id')} for variant_dict in product_dict.get('variants')]}
        validated, log_message = self.check_for_duplicate_sku_or_barcode_in_marketplace_product(mk_instance_id.sync_product_with, listing_item_validation_dict)
        if not validated:
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False
        existing_mk_product, existing_odoo_product, odoo_product_template = self.get_woocommerce_existing_mk_listing_and_odoo_product(variant_list, mk_instance_id)
        # This is fix for this Case: While user delete listing all items but not delete listing then while re-sync the product we get error of not found 'odoo_product_template'.
        if not odoo_product_template and mk_listing_id:
            odoo_product_template = mk_listing_id.product_tmpl_id

        if len(odoo_product_template) > 1:
            log_message = "IMPORT LISTING: Found multiple Odoo Product ({}) For WooCommerce Product : {}.".format(','.join([x.name for x in odoo_product_template]),
                                                                                                                  product_dict.get("title", product_dict.get("name")))
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False

        validated, log_message = self.check_validation_for_import_product(mk_instance_id.sync_product_with, listing_item_validation_dict, odoo_product_template,
                                                                          existing_odoo_product, existing_mk_product)
        if not validated:
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False

        product_category_ids = self.env['woo.product.category.ts'].woocommerce_get_product_category(product_dict.get('categories', []), mk_instance_id)
        product_tag_ids = self.env['woo.product.tags.ts'].woocommerce_get_product_tags(product_dict.get('tags', []), mk_instance_id)
        for variant_dict in variant_list:
            variant_id = variant_dict.get("id", "")
            variant_sku = variant_dict.get("sku") or False
            variant_price = variant_dict.get('price')
            listing_item_id = existing_mk_product.get(variant_id, False)
            odoo_product_id = existing_odoo_product.get(variant_id, False)
            variant_weight = variant_dict.get('weight', 0.0) or 0.0
            to_uom_id = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
            if not listing_item_id:
                if not mk_listing_id:
                    if not odoo_product_template and not mk_instance_id.is_create_products:
                        log_message = "IMPORT LISTING: Odoo Product not found for WooCommerce Product : {} and SKU: {}".format(product_dict.get("title", product_dict.get("name")),
                                                                                                                               variant_sku)
                        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='import', mk_log_line_dict={
                            'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        break
                    if not odoo_product_template:
                        product_attributes = [item for item in product_dict.get('attributes') if item.get('variation') == True]
                        if len(variant_list) > 1 and sorted([att.get('name') for att in product_attributes]) != sorted(
                                [att.get('name') for att in variant_dict.get('attributes')]):
                            missing = list(
                                set(sorted([att.get('name') for att in product_dict.get('attributes')])) - set(sorted([att.get('name') for att in variant_dict.get('attributes')])))
                            log_message = "IMPORT LISTING: Didn't found properly attributes({}). Please check product({}) variations and set properly in wooCommerce website.".format(missing,
                                                                                                                                                                                      variant_sku)
                            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={
                                'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                            break

                        odoo_product_template = self.create_odoo_template_for_woocommerce_product(product_dict, existing_odoo_product, mk_instance_id, product_category_ids,
                                                                                                  update_product_price)
                        odoo_product_id = existing_odoo_product.get(variant_id, False)
                    if not odoo_product_id:
                        log_message = "IMPORT LISTING ITEM: Odoo Product {} found but Odoo Product Variant not found for WooCommerce Product Variant : {} and SKU: {} " \
                                      "This may be due to SKU not configured properly on the WooCommerce. ".format(odoo_product_template.name,
                                                                                                                   product_dict.get("title", product_dict.get("name")),
                                                                                                                   variant_sku)
                        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={
                            'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        continue
                    listing_vals = self.prepare_marketplace_listing_vals_for_woocommerce(mk_instance_id, product_dict, variant_list, odoo_product_id, product_category_ids,
                                                                                         product_tag_ids)
                    mk_listing_id = self.create(listing_vals)
                    log_message = 'IMPORT LISTING: {} successfully created'.format(mk_listing_id.name)
                    self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={
                        'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                if not odoo_product_id:
                    if not mk_instance_id.is_create_products:
                        log_message = "IMPORT LISTING ITEM: Odoo Product Variant not found for WooCommerce Product Variant : {} and SKU: {}".format(
                            product_dict.get("title", product_dict.get("name")), variant_sku)
                        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={
                            'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        continue
                    if odoo_product_template.attribute_line_ids:
                        attribute_ids = self.env["product.attribute"]
                        odoo_attributes = odoo_product_template.attribute_line_ids.attribute_id
                        for attribute in product_dict.get('attributes'):
                            attribute_id = self.env["product.attribute"].search([('name', '=ilike', attribute["name"]), ('create_variant', '=', 'always')], limit=1)
                            attribute_ids |= attribute_id
                        if odoo_attributes != attribute_ids or len(odoo_attributes) != len(product_dict.get('attributes')):
                            log_message = "IMPORT LISTING ITEM: Odoo attribute ({}) isn't matching with WooCommerce attributes ({}) for WooCommerce Product : {}".format(
                                ','.join(odoo_attributes.mapped('name')), ','.join(attribute_ids.mapped('name')), product_dict.get("name", ""))
                            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={
                                'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                            break
                        else:
                            attribute_dict = {}
                            for attribute in variant_dict.get('attributes'):
                                attribute_dict.update({attribute.get('name'): attribute.get('option')})
                            self.env['product.template.attribute.line'].create_or_update_ptal(attribute_dict, odoo_product_template)
                            odoo_product_id = self._find_odoo_product_from_marketplace_attribute(attribute_dict, odoo_product_template)
                            odoo_prod_vals = {'default_code': variant_sku}
                            odoo_product_id.write(odoo_prod_vals)
                    if not odoo_product_id:
                        log_message = "IMPORT LISTING ITEM: Non Variation Odoo Product {} found. \n1. You have to add Attribute and Values in Odoo Product.\n2. Set Sku " \
                                      "According to the WooCommerce Variants.\n3. Try to re-sync again.".format(odoo_product_template.name)
                        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={
                            'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        continue
                mk_listing_item_vals = self.prepare_marketplace_listing_item_vals_for_woocommerce(product_dict, variant_dict, mk_instance_id, odoo_product_id, mk_listing_id)
                mk_listing_item_vals.update({'sequence': variant_sequence})
                if variant_weight:
                    converted_weight = mk_instance_id.woo_weight_uom_id._compute_quantity(float(variant_weight), to_uom_id, round=False)
                    if odoo_product_id and not odoo_product_id.weight:
                        odoo_product_id.weight = converted_weight
                listing_item_id = mk_listing_item_obj.create(mk_listing_item_vals)
                variant_sequence += 1
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={'success': [
                    {'log_message': 'IMPORT LISTING ITEM: {} ({}) successfully created'.format(mk_listing_id.name, listing_item_id.mk_id),
                     'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            else:
                if not listing_updated:
                    listing_vals = self.prepare_marketplace_listing_vals_for_woocommerce(mk_instance_id, product_dict, variant_list, odoo_product_id, product_category_ids,
                                                                                         product_tag_ids)
                    mk_listing_id.write(listing_vals)
                    listing_updated = True
                mk_listing_item_vals = self.prepare_marketplace_listing_item_vals_for_woocommerce(product_dict, variant_dict, mk_instance_id,
                                                                                                  odoo_product_id or listing_item_id.product_id, mk_listing_id)
                listing_item_id.write(mk_listing_item_vals)
                if variant_weight:
                    converted_weight = mk_instance_id.woo_weight_uom_id._compute_quantity(float(variant_dict.get('weight', 0.0)), to_uom_id, round=False)
                    if odoo_product_id and not odoo_product_id.weight:
                        odoo_product_id.weight = converted_weight
                variant_sequence += 1
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id, mk_log_line_dict={'success': [
                    {'log_message': 'IMPORT LISTING: {}[{}] successfully updated.'.format(listing_item_id.name, listing_item_id.default_code),
                     'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            if not variant_price:
                variant_price = 0
            if update_product_price:
                listing_item_id.create_or_update_pricelist_item(float(variant_price), update_product_price=update_product_price, skip_conversion=True)
            if not existing_mk_product or woo_update_image:
                self.set_woocommerce_listing_item_image(mk_instance_id, mk_listing_id, listing_item_id, variant_dict,woo_update_image= woo_update_image)
        #This is image setup for the listing not item.
        if not existing_mk_product or woo_update_image:
            self.set_woocommerce_listing_item_image(mk_instance_id, mk_listing_id, mk_listing_item_obj, product_dict,woo_update_image= woo_update_image)
        if len(product_dict.get('variants')) != mk_listing_id.item_count:
            mk_id_list = [str(variant_dict.get('id')) for variant_dict in product_dict.get('variants')]
            mk_listing_id.remove_extra_listing_item(mk_id_list)

        if is_update_existing_products and 'meta_data' in product_dict:
            custom_field_mapping_ids = mk_instance_id.custom_field_mapping_ids.filtered(
                lambda meta: meta.woo_operation == 'import_listing')
            product_template = mk_listing_id.product_tmpl_id
            product_variants = mk_listing_id.listing_item_ids.mapped('product_id')

            for custom_field_mapping in custom_field_mapping_ids.filtered(
                    lambda meta: meta.model_id.model == product_template._name):
                mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                product_dict.get('meta_data'), "import", product_template)

            for custom_field_mapping in custom_field_mapping_ids.filtered(
                    lambda meta: meta.model_id.model == product_variants._name):
                mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                product_dict.get('meta_data'), "import", product_variants)
        return mk_listing_id

    def prepare_woocommerce_attribute_line_vals(self, product_dict):
        product_attribute_obj = self.env['product.attribute']
        product_attribute_value_obj = self.env['product.attribute.value']
        attribute_line_vals = []
        variant_list = product_dict.get("variants")
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        if len(variant_list) > 1:
            for product_attribute_dict in product_dict.get("attributes", ""):
                #attribute without variant creation(in woocommerce)
                if not product_attribute_dict.get("variation"):
                    continue
                attribute_name = product_attribute_dict.get("name", "")
                attribute_values = product_attribute_dict.get('options', '')
                product_attribute_id = product_attribute_obj.search([("name", "=ilike", attribute_name)], limit=1)
                #Below code when variants create instant but attribute has Dynamically or Never.
                if product_attribute_id and product_attribute_id.create_variant != 'always':
                    mk_instance_id = self.env.context.get('mk_instance_id') or mk_log_id.mk_instance_id
                    log_message = "IMPORT LISTING ITEM: The Variants Creation Mode for the attribute {attribute_name} is not set to 'Instantly.' You will need to create products manually to sync products with {attribute_name} attribute.".format(
                        attribute_name=product_attribute_id.name)
                    self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id,
                                                         mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    return False
                if not product_attribute_id:
                    product_attribute_id = product_attribute_obj.create({"name": attribute_name})

                product_attribute_value_id_list = []
                for attribute_value in attribute_values:
                    attrib_value = product_attribute_value_obj.search([("attribute_id", "=", product_attribute_id.id), ("name", "=ilike", attribute_value)], limit=1)
                    if not attrib_value:
                        attrib_value = product_attribute_value_obj.with_context(active_id=False).create({"attribute_id": product_attribute_id.id, "name": attribute_value})
                    product_attribute_value_id_list.append(attrib_value.id)

                if product_attribute_value_id_list:
                    attribute_line_ids_data = [0, False, {"attribute_id": product_attribute_id.id, "value_ids": [[6, False, product_attribute_value_id_list]]}]
                    attribute_line_vals.append(attribute_line_ids_data)
        return attribute_line_vals

    def create_odoo_template_for_woocommerce_product(self, product_dict, existing_odoo_product, mk_instance_id, product_category_ids, update_product_price):
        odoo_template_obj = self.env["product.template"]
        product_title = product_dict.get("name", "")
        variant_list = product_dict.get("variants")
        attribute_line_vals = self.prepare_woocommerce_attribute_line_vals(product_dict)
        product_template_vals = {
            'name': product_title,
            'is_storable': True,
            'attribute_line_ids': attribute_line_vals,
            # 'description_sale': product_dict.get("description", "")
        }
        if mk_instance_id.is_update_odoo_product_category and product_category_ids and len(product_category_ids) == 1:
            odoo_categ_id = self.env['product.category'].search([('name', '=', product_category_ids.name)], limit=1)
            if odoo_categ_id:
                product_template_vals.update({'categ_id': odoo_categ_id.id})

        product_tmpl_id = odoo_template_obj.create(product_template_vals)
        if len(variant_list) > 1:
            # Adding SKU in newly created variants according to marketplace variant's information.
            for variant_dict in variant_list:
                attribute_dict = {}
                for attribute in variant_dict.get('attributes'):
                    attribute_dict.update({attribute.get('name'): attribute.get('option')})
                odoo_product_id = self._find_odoo_product_from_marketplace_attribute(attribute_dict, product_tmpl_id)
                product_update_vals = {'default_code': variant_dict.get('sku')}
                odoo_product_id.write(product_update_vals)
                existing_odoo_product.update({variant_dict.get('id'): odoo_product_id})
        else:
            product_tml_update_vals = {'default_code': variant_list[0].get('sku')}
            if variant_list[0].get('barcode'):
                product_tml_update_vals.update({'barcode': variant_list[0].get('barcode')})
            price = variant_list[0].get('price')
            if price and update_product_price:
                if mk_instance_id.pricelist_id.currency_id.id == product_tmpl_id.company_id.currency_id.id:
                    product_tml_update_vals.update({'list_price': float(price)})
                else:
                    mk_instance_currency_id = mk_instance_id.pricelist_id.currency_id
                    odoo_product_company_currency_id = product_tmpl_id.company_id.currency_id
                    price_currency = mk_instance_currency_id._convert(float(price), odoo_product_company_currency_id, self.env.user.company_id, fields.Date.today())
                    product_tml_update_vals.update({'list_price': price_currency})
            converted_weight = self._marketplace_convert_weight_woocommerce(variant_list[0].get('weight'), variant_list[0].get('weight_unit'))
            if converted_weight and product_tmpl_id:
                product_tml_update_vals.update({'weight': converted_weight})
            product_tmpl_id.write(product_tml_update_vals)
            existing_odoo_product.update({variant_list[0].get('id'): product_tmpl_id.product_variant_ids})
        return product_tmpl_id

    def woocommerce_import_listings(self, mk_instance_id, from_listing_date=False, to_listing_date=False, mk_listing_id=False, update_product_price=False, woo_update_image = False,update_existing_product=False, status='any'):
        is_delete_not_in_marketplace_listing = False
        res_id_list, action = [], False
        if mk_listing_id:
            proudct_list = []
            mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
            mk_log_id = self.env.context.get('mk_log_id', False) or self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
            for product in ''.join(mk_listing_id.split()).split(','):
                product_response, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'products/{}'.format(product))
                if status_code in [200, 201]:
                    proudct_list += product_response
            for product_dict in proudct_list:
                mk_listing_id = self.with_context(mk_log_line_dict=mk_log_line_dict, mk_log_id=mk_log_id).create_update_woocommerce_product(product_dict, mk_instance_id,update_product_price=update_product_price,woo_update_image=woo_update_image,is_update_existing_products=update_existing_product)
                mk_listing_id and res_id_list.append(mk_listing_id.id)
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
            if res_id_list:
                action = {'res_model': 'mk.listing', 'type': 'ir.actions.act_window', 'target': 'current'}
                if len(res_id_list) == 1:
                    action.update({'view_mode': 'form', 'res_id': res_id_list[0]})
                else:
                    action.update({'name': _('WooCommerce Listings'), 'domain': [('id', 'in', res_id_list)], 'view_mode': 'list,form'})
                return action
            return mk_listing_id
        else:
            params = {'status': status}
            if from_listing_date or to_listing_date:
                from_listing_date and params.update({'after': str(from_listing_date)})
                to_listing_date and params.update({'before': str(to_listing_date)})
            else:
                is_delete_not_in_marketplace_listing = True
            product_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'products', params=params)
            if status_code not in [200, 201]:
                if isinstance(product_list,list):
                    product_list = str(product_list)
                raise UserError(_(product_list))

        if product_list:
            listing_to_delete = self.env['mk.listing']
            if is_delete_not_in_marketplace_listing:
                listing_to_delete = self.search([('mk_instance_id', '=', mk_instance_id.id)])
            batch_size = mk_instance_id.queue_batch_limit or 100
            for product_list in tools.split_every(batch_size, product_list):
                queue_id = mk_instance_id.with_context(update_product_price=update_product_price, update_existing_product=update_existing_product, woo_update_image=woo_update_image).action_create_queue(
                    type='product')
                for product_dict in product_list:
                    name = product_dict.get('name', '') or ''
                    line_vals = {
                        'mk_id': product_dict.get('id') or '',
                        'state': 'draft',
                        'name': name.strip(),
                        'data_to_process': pprint.pformat(product_dict),
                        'mk_instance_id': mk_instance_id.id,
                    }
                    if is_delete_not_in_marketplace_listing:
                        listing_to_delete -= listing_to_delete.filtered(lambda x: x.mk_id == str(product_dict.get('id')))
                    queue_id.action_create_queue_lines(line_vals)
                res_id_list.append(queue_id.id)
            if listing_to_delete:
                listing_to_delete.unlink()
        mk_instance_id.last_listing_import_date = fields.Datetime.now()
        if res_id_list:
            action = {'res_model': 'mk.queue.job', 'type': 'ir.actions.act_window', 'target': 'current'}
            if len(res_id_list) == 1:
                action.update({'view_mode': 'form', 'res_id': res_id_list[0]})
            else:
                action.update({'name': _('WooCommerce Listing Queue'), 'domain': [('id', 'in', res_id_list)], 'view_mode': 'list,form'})
        return action

    def woocommerce_open_listing_in_marketplace(self):
        marketplace_url = self.mk_instance_id.woo_shop_url + '/wp-admin/post.php?post=' + self.mk_id + '&action=edit'
        return marketplace_url

    def set_woocommerce_listing_item_image(self, mk_instance_id, mk_listing_id, listing_item_id, variant_dict,woo_update_image = False):
        image_list = variant_dict.get('image', []) and [variant_dict.get('image')] or variant_dict.get('images', [])
        mk_listing_image = self.env['mk.listing.image']
        for i, image_vals in enumerate(image_list):
            image_url = image_vals.get('src')
            if not image_url:
                continue
            image_id = image_vals.get('id')
            if listing_item_id:
                listing_image_id = mk_listing_image.search([('mk_id', '=', image_id), ('mk_instance_id', '=', mk_instance_id.id), ('mk_listing_item_ids', 'in', listing_item_id.ids)])
            else:
                listing_image_id = mk_listing_image.search([('mk_id', '=', image_id), ('mk_instance_id', '=', mk_instance_id.id)])
            headers = requests.utils.default_headers()
            headers.update({'User-Agent': 'Odoo Connection'})
            try:
                image_binary = base64.b64encode(requests.get(image_url, headers=headers).content)
                vals = {
                    'name': mk_listing_id.name,
                    'mk_id': image_id,
                    'image': image_binary,
                    'mk_instance_id': mk_instance_id.id,
                    'mk_listing_id': mk_listing_id.id,
                }
                if listing_item_id:
                    vals.update({'mk_listing_item_ids': [(6, 0, listing_item_id.ids)]})
                if listing_image_id:
                    listing_image_id.write(vals)
                else:
                    mk_listing_image.create(vals)

                if i == 0 and not listing_item_id and (not mk_listing_id.product_tmpl_id.image_1920 or woo_update_image):
                    mk_listing_id.product_tmpl_id.write({'image_1920': image_binary})

                if listing_item_id and (not listing_item_id.product_id.image_variant_1920 or woo_update_image):
                    listing_item_id.product_id.write({'image_variant_1920': image_binary})
            except Exception:
                pass
        return True

    def cron_auto_import_woocommerce_stock(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        self.woocommerce_import_stock(mk_instance_id)
        return True

    def cron_auto_export_woocommerce_stock(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        self.woocommerce_export_stock(mk_instance_id)
        return True

    def create_woo_process_inventory_adjustment(self, inventory_level_list, mk_instance_id, odoo_location_id, mk_log_id):
        product_variant_ids, inventory_quants = self.env['product.product'], self.env['stock.quant']
        for inventory_level in inventory_level_list:
            mk_id = inventory_level.get('id')
            qty = inventory_level.get('stock_quantity', 0) or 0
            mk_listing_item_id = self.env['mk.listing.item'].search(
                [('product_id.type', '!=', 'service'), ('product_id.tracking', '=', 'none'), ('is_listed', '=', True), ('mk_id', '=', mk_id),
                 ('mk_instance_id', '=', mk_instance_id.id)],
                limit=1)
            if mk_listing_item_id:
                odoo_product_id = mk_listing_item_id.product_id
                product_variant_ids += odoo_product_id
                inventory_quants.create_or_update_inventory_quant(odoo_location_id.id, odoo_product_id, qty, name="Woo Inventory ({} on {})".format(mk_instance_id.name, datetime.now().strftime(DF)),
                                                                  auto_validate=mk_instance_id.is_validate_adjustment)
                # inventory_quant = self.woocommerce_create_update_product_quant(odoo_product_id, odoo_location_id, qty, mk_log_id)
                log_message = "IMPORT STOCK: Product {} updated to {} quantity with {} location.".format(odoo_product_id.display_name, qty, odoo_location_id.display_name)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message}]})
        return True

    def woocommerce_import_stock(self, mk_instance_id):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
        listing_item_ids = self.env['mk.listing.item'].search([('mk_instance_id', '=', mk_instance_id.id), ('is_listed', '=', True)])
        if listing_item_ids:
            warehouse_id = mk_instance_id.warehouse_id
            if not warehouse_id:
                log_message = "IMPORT STOCK: No warehouse found from Marketplace Instance {} at the time of Import stock!".format(mk_instance_id.name)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                return False
            odoo_location_id = warehouse_id.lot_stock_id
            sku_list = listing_item_ids.mapped('default_code')
            inventory_levels = []
            for batch_sku in split_every(100, sku_list):
                params = {'sku': ",".join(batch_sku), '_fields': 'id, name, sku, stock_quantity, manage_stock'}
                if mk_instance_id.last_stock_import_date:
                    params.update({'modified_after': str(mk_instance_id.last_stock_import_date)})
                response_list, status = mk_instance_id.fetch_data_from_woocommerce('get', 'products', params=params)
                inventory_levels += response_list
            try:
                self.create_woo_process_inventory_adjustment(inventory_levels, mk_instance_id, odoo_location_id, mk_log_id)
            except Exception as e:
                log_message = "IMPORT STOCK: Error while Import Stock. ERROR: {}".format(e)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import', mk_log_line_dict={'error': [{'log_message': log_message}]})
                return False
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        mk_instance_id.last_stock_import_date = fields.Datetime.now()
        return True

    def prepare_vals_for_export_stock_to_woocommerce(self, mk_instance_id, mk_listing_item_ids):
        update_variant_list = []
        for listing_item_id in mk_listing_item_ids:
            variant_vals = {}
            variant_quantity = listing_item_id.product_id.get_product_stock(listing_item_id.export_qty_type, listing_item_id.export_qty_value,
                                                                            mk_instance_id.warehouse_id.lot_stock_id,
                                                                            mk_instance_id.stock_field_id.name)
            variant_vals.update({'stock_quantity': variant_quantity, 'manage_stock': listing_item_id.woo_manage_stock, 'id': listing_item_id.mk_id})
            update_variant_list.append(variant_vals)
        return update_variant_list

    def prepare_variant_data_by_product(self, listing_item_ids):
        listing_item_by_listing_list = {mk_listing: list(listing_items) for mk_listing, listing_items in groupby(listing_item_ids, key=lambda rec: rec.mk_listing_id)}
        return listing_item_by_listing_list

    def woocommerce_export_stock(self, mk_instance_id):
        mk_listing_item_obj = self.env['mk.listing.item']
        listing_item_ids = self.get_mk_listing_item(mk_instance_id)
        if isinstance(listing_item_ids, list):
            listing_item_ids = self.env['mk.listing.item'].browse(listing_item_ids)
        if listing_item_ids:
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
            mk_log_line_dict = {'error': [], 'success': []}
            listing_item_ids = listing_item_ids.filtered(lambda x: x.woo_manage_stock and (not x.woocommerce_last_stock_update_date or (
                    mk_instance_id.last_stock_update_date and x.woocommerce_last_stock_update_date <= mk_instance_id.last_stock_update_date)))
            for listing_batch in tools.split_every(50, listing_item_ids, piece_maker=list):
                listing_item_by_listing_dict = self.prepare_variant_data_by_product(listing_batch)
                for mk_listing_id, mk_listing_item_ids in listing_item_by_listing_dict.items():
                    update_variant_list = mk_listing_id.prepare_vals_for_export_stock_to_woocommerce(mk_instance_id, mk_listing_item_ids)
                    # Two endpoints because (product/listing) and (product variant/listing items) stock have separate endpoints in WooCommerce sometimes(not sure).
                    if mk_listing_item_ids and len(mk_listing_item_ids) == 1 and mk_listing_item_ids[0].mk_id == mk_listing_id.mk_id:
                        # This request sent when only template/listing in woocommerce
                        # This is only updating stock in the template/listing in woocommerce
                        variant_response, status_code = update_variant_list and mk_instance_id.fetch_data_from_woocommerce('post', "products/batch".format(mk_listing_id.mk_id),
                                                                                                                           data={'update': update_variant_list})
                    else:
                        # This request sent when when template with variants/listing items in woocommerce
                        # This is only updating stock in the variants/listing items in woocommerce
                        variant_response, status_code = update_variant_list and mk_instance_id.fetch_data_from_woocommerce('post', "products/{}/variations/batch".format(mk_listing_id.mk_id),
                                                                                                                       data={'update': update_variant_list})
                    if status_code == 400:
                        ##Getting error code 400 and response have code 401: do not have access rights for the update product from Odoo to woo
                        error_dict = eval(variant_response.response.content)
                        log_message = f"UPDATE STOCK: Error while trying to export stock for WooCommerce. \n Message : {error_dict.get('message', 'Permission issue')} \n Error : {error_dict.get('code', '')} {error_dict.get('data', '')}."
                        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                        return False
                    if status_code not in [200, 201]:
                        log_message = "Error while trying to export stock for WooCommerce Product: {}, ERROR: {}.".format(mk_listing_id.name, variant_response)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                        continue
                    listing_item_response = variant_response[0].get('update')
                    if listing_item_response and listing_item_response[0].get('error', False):
                        log_message = "Error while trying to export stock for WooCommerce Product: {}, ERROR: {}.".format(mk_listing_id.name,
                                                                                                                          listing_item_response[0].get('error').get('message'))
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                        continue
                    log_message = "Successfully Updated stock of {} product in WooCommerce.".format(mk_listing_id.name)
                    mk_log_line_dict['success'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                    for variant_dict in listing_item_response:
                        listing_item_id = self.env['mk.listing.item'].search([('default_code', '=ilike', variant_dict.get('sku')), ('mk_instance_id', '=', mk_instance_id.id)])
                        if not listing_item_id:
                            continue
                        listing_item_id.write(
                            {'woo_manage_stock': variant_dict.get('manage_stock', True),
                             'item_update_date': convert_woocommerce_datetime_to_utc(variant_dict.get("date_modified", "")),
                             'woocommerce_last_stock_update_date': fields.Datetime.now()})
                self.env.cr.commit()
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
            if not mk_log_id.log_line_ids:
                mk_log_id.unlink()
        mk_instance_id.write({'last_stock_update_date': fields.Datetime.now()})
        return True

    def prepare_woocommerce_category_vals_for_product_export(self):
        woo_categ_ids = self.woo_categ_ids.mapped('mk_id')
        woo_categ_vals = [{'id': cat_id} for cat_id in woo_categ_ids]
        return {'categories': woo_categ_vals}

    def import_export_odoo_woocommerce_categories(self):
        woo_categ_obj = self.env['woo.product.category.ts']
        remaining_to_export_woo_categ_ids = self.woo_categ_ids.filtered(lambda x: not x.exported_in_woocommerce)
        if not remaining_to_export_woo_categ_ids:
            return self.prepare_woocommerce_category_vals_for_product_export()
        woo_categ_obj.import_woocommerce_categories(self.mk_instance_id)
        remaining_to_export_woo_categ_ids = self.woo_categ_ids.filtered(lambda x: not x.exported_in_woocommerce)
        if remaining_to_export_woo_categ_ids:
            remaining_to_export_woo_categ_ids = woo_categ_obj.export_categories_to_woocommerce(remaining_to_export_woo_categ_ids)
        return remaining_to_export_woo_categ_ids

    def prepare_woocommerce_tags_vals_for_product_export(self):
        woo_tag_ids = self.woo_tag_ids.mapped('mk_id')
        woo_tag_vals = [{'id': tag_id} for tag_id in woo_tag_ids]
        return {'tags': woo_tag_vals}

    def import_export_odoo_woocommerce_tags(self):
        woo_tags_obj = self.env['woo.product.tags.ts']
        remaining_to_export_woo_tag_ids = self.woo_tag_ids.filtered(lambda x: not x.exported_in_woocommerce)
        if not remaining_to_export_woo_tag_ids:
            return self.prepare_woocommerce_tags_vals_for_product_export()
        woo_tags_obj.import_woocommerce_tags(self.mk_instance_id)
        remaining_to_export_woo_tag_ids = self.woo_tag_ids.filtered(lambda x: not x.exported_in_woocommerce)
        if remaining_to_export_woo_tag_ids:
            remaining_to_export_woo_tag_ids = woo_tags_obj.export_tags_to_woocommerce(remaining_to_export_woo_tag_ids, self.mk_instance_id)
        return remaining_to_export_woo_tag_ids

    def get_woocommerce_product_attributes(self):
        woo_attribute_obj = self.env['woo.product.attribute']
        woo_attribute_list, position = [], 0
        for attribute_line_id in self.product_tmpl_id.attribute_line_ids:
            attribute_value_list = attribute_line_id.value_ids.mapped('name')
            woo_attribute_id = woo_attribute_obj.search(
                [('attribute_id', '=', attribute_line_id.attribute_id.id), ('mk_instance_id', '=', self.mk_instance_id.id), ('exported_in_woocommerce', '=', True)],limit=1)
            if not woo_attribute_id:
                woo_attribute_id = woo_attribute_obj.get_product_attribute_from_woocommerce(self.mk_instance_id,
                                                                                            attribute_line_id.attribute_id)  # Added attribute_line_id args if not found then export it woo to odoo
            # woo_attribute_id = woo_attribute_obj.search(
            #     [('attribute_id', '=', attribute_line_id.attribute_id.id), ('mk_instance_id', '=', self.mk_instance_id.id), ('exported_in_woocommerce', '=', True)])
            if woo_attribute_id:
                woo_attribute_list.append({"id": woo_attribute_id.mk_id,
                                           "name": attribute_line_id.attribute_id.name,
                                           "position": position,
                                           "visible": True,
                                           "variation": True,
                                           "options": attribute_value_list})
                position += 1
        return woo_attribute_list

    def get_listing_item_price_for_woocommerce(self, listing_item_id):
        variant_price = self.mk_instance_id.pricelist_id.with_context(uom=listing_item_id.product_id.uom_id.id)._get_product_price(listing_item_id.product_id, quantity=1.0, currency=False)
        return {'regular_price': str(variant_price), 'sale_price': str(variant_price)}

    def prepare_update_vals_for_woocommerce_template(self, operation_wizard):
        self.ensure_one()
        product_tmpl_dict = {}
        mk_instance_id = self.mk_instance_id
        product_tmpl_id = self.product_tmpl_id
        price_unit_prec = self.env['decimal.precision'].precision_get('Product Price')

        variant = True if self.item_count > 1 else False

        if operation_wizard.is_update_product:
            product_tmpl_dict.update({'name': self.name, 'type': 'variable' if variant else 'simple'})
            if self.description:
                product_tmpl_dict.update({'description': self.with_context(lang=mk_instance_id.lang).description})
            if self.short_description:
                product_tmpl_dict.update({'short_description': self.with_context(lang=mk_instance_id.lang).short_description})

            if self.woo_categ_ids:
                self.import_export_odoo_woocommerce_categories()
                product_tmpl_dict.update({'categories': [{'id': int(mk_id)} for mk_id in self.woo_categ_ids.mapped('mk_id')]})

            if self.woo_tag_ids:
                self.import_export_odoo_woocommerce_tags()
                product_tmpl_dict.update({'tags': [{'id': int(mk_id)} for mk_id in self.woo_tag_ids.mapped('mk_id')]})

            weight = self._marketplace_convert_weight_woocommerce(product_tmpl_id.weight, self.mk_instance_id.woo_weight_uom_id.name, reverse=True)
            if weight:
                product_tmpl_dict.update({'weight': str(weight)})

            # Update product template attribute. If not found then export to Woo.
            woo_attribute_list = self.get_woocommerce_product_attributes()
            woo_attribute_list and product_tmpl_dict.update({'attributes': woo_attribute_list})

        listing_item_id = self.listing_item_ids
        if not variant and operation_wizard.is_update_product:
            product_tmpl_dict.update({'sku': listing_item_id.default_code, 'manage_stock': listing_item_id.woo_manage_stock})

        if operation_wizard.publish_in_store:
            product_tmpl_dict.update({'status': operation_wizard.publish_in_store})

        if operation_wizard.is_set_images:
            #We are only exporting image which is not existing on listing items on wooCommerce listing.
            image_ids = self.image_ids and (self.image_ids.filtered(lambda x: not x.mk_listing_item_ids) or self.image_ids[0]) or self.image_ids
            for image_rec in image_ids:
                if product_tmpl_dict.get('images', False):
                    if image_rec.mk_id:
                        pred_vals = {'id': int(image_rec.mk_id), 'alt': image_rec.woo_alt_text, 'name': image_rec.name}
                    else:
                        #Before changing updating name check image data that we are updating according to woocommerce product.
                        pred_vals = {'src': image_rec.url or '', 'alt': image_rec.woo_alt_text or '', 'name': str(image_rec.id)}
                    product_tmpl_dict['images'].append(pred_vals)
                else:
                    # Before changing updating name check image data that we are updating according to woocommerce product.
                    product_tmpl_dict.update({'images': [{'id': int(image_rec.mk_id)}]}) if image_rec.mk_id else product_tmpl_dict.update(
                        {'images': [{'src': image_rec.url or '', 'alt': image_rec.woo_alt_text or '', 'name': str(image_rec.id)}]})

        if not variant:
            if operation_wizard.is_set_price:
                price_vals = self.get_listing_item_price_for_woocommerce(listing_item_id)
                if not float_is_zero(float(price_vals.get('regular_price')), precision_digits=price_unit_prec):
                    product_tmpl_dict.update(price_vals)

            if operation_wizard.is_set_quantity and listing_item_id.woo_manage_stock:
                variant_quantity = listing_item_id.product_id.get_product_stock(listing_item_id.export_qty_type, listing_item_id.export_qty_value,
                                                                                mk_instance_id.warehouse_id.lot_stock_id,
                                                                                mk_instance_id.stock_field_id.name)
                product_tmpl_dict.update({'stock_quantity': variant_quantity})
        return product_tmpl_dict

    def prepare_vals_for_update_woocommerce_listing_items(self, product_tmpl_dict, operation_wizard):
        if len(self.listing_item_ids) == 1:
            return [], []
        price_unit_prec = self.env['decimal.precision'].precision_get('Product Price')
        mk_instance_id, update_variant_list, create_variant_list, woo_attribute_obj = self.mk_instance_id, [], [], self.env['woo.product.attribute']
        for listing_item_id in self.listing_item_ids:
            variant_vals, attribute_list = {}, []
            if operation_wizard.is_update_product:
                variant_vals.update({'sku': listing_item_id.default_code,
                                     'manage_stock': listing_item_id.woo_manage_stock})
                weight = self._marketplace_convert_weight_woocommerce(listing_item_id.product_id.weight, listing_item_id.product_id.weight_uom_name, mk_instance_id=mk_instance_id)
                weight and variant_vals.update({'weight': str(weight)})
                for atts in listing_item_id.product_id.product_template_attribute_value_ids:
                    woo_attribute_id = woo_attribute_obj.search(
                        [('name', '=', atts.attribute_id.name), ('exported_in_woocommerce', '=', True), ('mk_instance_id', '=', mk_instance_id.id)])
                    woo_attribute_id and attribute_list.append({'id': woo_attribute_id.mk_id, 'option': atts.name})
                if attribute_list:
                    variant_vals.update({'attributes': attribute_list})

            if operation_wizard.is_set_images:
                #We are only able to export and set one variant image. so we will set first one.
                for image_rec in listing_item_id.image_ids:
                    # Before changing updating name check image data that we are updating according to woocommerce product.
                    variant_vals.update({'image': {'id': int(image_rec.mk_id)}}) if image_rec.mk_id else variant_vals.update(
                            {'image': {'src': image_rec.url or '', 'alt': image_rec.woo_alt_text or '', 'name': str(image_rec.id)}})
                    break

            # set price
            if operation_wizard.is_set_price:
                price_vals = self.get_listing_item_price_for_woocommerce(listing_item_id)
                if not float_is_zero(float(price_vals.get('regular_price')), precision_digits=price_unit_prec):
                    variant_vals.update(price_vals)
            # set quantity
            if operation_wizard.is_set_quantity and listing_item_id.woo_manage_stock:
                variant_quantity = listing_item_id.product_id.get_product_stock(listing_item_id.export_qty_type, listing_item_id.export_qty_value,
                                                                                mk_instance_id.warehouse_id.lot_stock_id,
                                                                                mk_instance_id.stock_field_id.name)
                variant_vals.update({'stock_quantity': variant_quantity})
            if listing_item_id.mk_id:
                variant_vals.update({'id': listing_item_id.mk_id})
                update_variant_list.append(variant_vals)
            else:
                not create_variant_list and product_tmpl_dict.update({'default_attributes': variant_vals.get('attributes')})
                create_variant_list.append(variant_vals)
        return create_variant_list, update_variant_list

    def process_woocommerce_response(self, variant_response, data_type):
        status_list, response_list = [], []
        for type in data_type:
            status_list += [variant_dict.get('error', {}).get('data', {}).get('status', 200) for variant_dict in variant_response[0].get(type)]
            response_list += [variant_dict for variant_dict in variant_response[0].get(type)]
        return status_list, response_list

    def export_woocommerce_listing_item_data(self, listing_id, create_variant_list, update_variant_list):
        if not create_variant_list and not update_variant_list:
            return []
        data, data_type, success_response_list = {}, [], []
        mk_log_id = self.env.context.get('mk_log_id', False)
        if create_variant_list:
            data.update({'create': create_variant_list})
            data_type.append('create')
        if update_variant_list:
            data.update({'update': update_variant_list})
            data_type.append('update')

        variant_response, status_code = self.mk_instance_id.fetch_data_from_woocommerce('post', "products/{}/variations/batch".format(listing_id), data=data)
        if status_code == 400:
            error_dict = variant_response.response.content and eval(variant_response.response.content) or {}
            raise UserError(_(f"Message : {error_dict.get('message','Permission issue')} \n Error : {error_dict.get('code','')} {error_dict.get('data','')}"))
        status_list, response_list = self.process_woocommerce_response(variant_response, data_type)
        for response in response_list:
            if response.get('error'):
                log_message = 'UPDATE/EXPORT LISTING ITEM: Error while updating/exporting listing item. ERROR: {}'.format(response.get('error'))
                mk_log_id and self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='export', mk_log_line_dict={'error': [{'log_message': log_message}]})
                continue
            success_response_list.append(response)
        return success_response_list

    def update_odoo_woocommerce_listing_item(self, variant_response_list, operation_wizard=False):
        for variant_dict in variant_response_list:
            listing_item_id = self.env['mk.listing.item'].search([('default_code', '=ilike', variant_dict.get('sku')), ('mk_instance_id', '=', self.mk_instance_id.id)])
            if not listing_item_id:
                return False
            item_vals = {
                'mk_id': variant_dict.get('id'),
                'is_listed': True,
                'default_code': variant_dict.get("sku", ""),
                'woo_tax_class': variant_dict.get("tax_class", ""),
                'woo_manage_stock': variant_dict.get('manage_stock', True),
                'woo_tax_status': variant_dict.get("tax_status", "none"),
                'item_create_date': convert_woocommerce_datetime_to_utc(variant_dict.get("date_created", "")),
                'item_update_date': convert_woocommerce_datetime_to_utc(variant_dict.get("date_modified", "")),
            }
            if operation_wizard and operation_wizard.is_set_quantity and listing_item_id.woo_manage_stock:
                item_vals.update({'woocommerce_last_stock_update_date': fields.Datetime.now()})
            if operation_wizard and operation_wizard.is_set_images:
                if variant_dict.get('image'):
                    try:
                        #id we have given as name while exporting images
                        id_we_have_given_as_name = int(variant_dict.get('image').get('name'))
                        item_image = self.env['mk.listing.image'].search([('id','=',id_we_have_given_as_name)])
                        if not item_image.mk_id:
                            item_image.write({'mk_id':variant_dict['image']['id']})
                    except Exception as e:
                        pass
            listing_item_id.write(item_vals)

    def update_odoo_woocommerce_listing(self, product_response, operation_wizard=False):
        def sync_image(image_vals):
            for image_val in image_vals:
                img = self.env['mk.listing.image'].search([('mk_id', '=', image_val.get('id')), ('mk_listing_id', 'in', self.ids)])
                if not img:
                    try:
                        img = self.env['mk.listing.image'].search([('name', '=', int(image_val.get('name'))), ('mk_listing_id', 'in', self.ids)])
                        img.write({'mk_id': image_val.get('id')})
                    except ValueError:
                        continue

        product_published = True if product_response.get('status', '') == 'publish' else False
        listing_vals = {'mk_id': product_response.get('id'),
                        'listing_create_date': convert_woocommerce_datetime_to_utc(product_response.get("date_created", "")),
                        'listing_update_date': convert_woocommerce_datetime_to_utc(product_response.get("date_modified", "")),
                        'is_listed': True,
                        'number_of_variants_in_mk': len(self.listing_item_ids.filtered(lambda x: x.is_listed)),
                        'slug': product_response.get("slug", ""),
                        'is_published': product_published}
        product_response.get('images', False) and sync_image(product_response.get('images')) or ''

        #Below code to update last stock date on items. It is added because some product didn't have attributes and there is no item so we need to update
        #in in listing items
        if self.listing_item_ids and len(self.listing_item_ids) == 1 and operation_wizard and operation_wizard.is_set_quantity and self.listing_item_ids.woo_manage_stock:
            self.listing_item_ids.write({'woocommerce_last_stock_update_date': fields.Datetime.now()})

        return self.write(listing_vals)

    def update_odoo_woocommerce_listing_and_items(self, product_response, variant_response_list, operation_wizard=False):
        self.update_odoo_woocommerce_listing_item(variant_response_list or [product_response], operation_wizard=operation_wizard)
        self.update_odoo_woocommerce_listing(product_response, operation_wizard=operation_wizard)
        return True

    def woocommerce_export_listing_to_mk(self, operation_wizard):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=operation_wizard.mk_instance_id, operation_type='export')

        for listing_id in self:
            if listing_id.mapped('listing_item_ids').filtered(lambda x: not x.default_code):
                raise UserError(_("Please set Internal Reference in Variant of Product Name {}".format(listing_id.name)))

            product_tmpl_dict = listing_id.prepare_update_vals_for_woocommerce_template(operation_wizard)

            create_variant_list, update_variant_list = listing_id.prepare_vals_for_update_woocommerce_listing_items(product_tmpl_dict, operation_wizard)

            product_response, status_code = listing_id.mk_instance_id.fetch_data_from_woocommerce('post', "products", data=product_tmpl_dict)
            if status_code not in [200, 201]:
                raise ValidationError(_("WooCommerce: Error while exporting listing({}). Message: {}".format(listing_id.name, product_response[0])))
            product_response = product_response[0]

            variant_response = listing_id.with_context(mk_log_id=mk_log_id).export_woocommerce_listing_item_data(product_response.get('id'), create_variant_list, update_variant_list)
            listing_id.update_odoo_woocommerce_listing_and_items(product_response, variant_response, operation_wizard)
            listing_id.env.cr.commit()

        if mk_log_id.exists() and not mk_log_id.log_line_ids:
            mk_log_id.unlink()
        elif mk_log_id.exists():
            return operation_wizard.mk_instance_id.action_open_model_view(mk_log_id.ids, 'mk.log', 'Log')
        return True

    def woocommerce_update_listing_to_mk(self, operation_wizard):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=self.mk_instance_id, operation_type='export')
        for listing_batch in tools.split_every(100, self, piece_maker=list):
            update_listing_batch = []
            for mk_listing_id in listing_batch:
                listing_dict = mk_listing_id.prepare_update_vals_for_woocommerce_template(operation_wizard)

                listing_dict.update({'id': mk_listing_id.mk_id})
                update_listing_batch.append(listing_dict)
                create_variant_list, update_variant_list = mk_listing_id.prepare_vals_for_update_woocommerce_listing_items(listing_dict, operation_wizard)
                listing_item_response = mk_listing_id.with_context(mk_log_id=mk_log_id).export_woocommerce_listing_item_data(mk_listing_id.mk_id, create_variant_list, update_variant_list)
                mk_listing_id.update_odoo_woocommerce_listing_item(listing_item_response, operation_wizard)

            listing_response, status_code = self.mk_instance_id.fetch_data_from_woocommerce('post', "products/batch", data={'update': update_listing_batch})
            if status_code == 400:
                error_dict = listing_response.response.content and eval(listing_response.response.content) or {}
                raise UserError(_(f"Message : {error_dict.get('message', 'Permission issue')} \n Error : {error_dict.get('code', '')} {error_dict.get('data', '')}"))

            if status_code not in [200, 201]:
                log_message = _("WooCommerce: Error while updating product details. ERROR: {}".format(listing_response))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
            listing_response = listing_response[0].get('update')
            for listing_response_dict in listing_response:
                mk_listing = self.search([('mk_id', '=', listing_response_dict.get('id'))])
                if listing_response_dict.get('error', False):
                    log_message = "WooCommerce: Error while updating product details ({}). Message: {}".format(mk_listing.name or ', '.join(self.mapped('name')),listing_response_dict.get('error').get('message'))
                    self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                else:
                    mk_listing.update_odoo_woocommerce_listing(listing_response_dict, operation_wizard)
                self.env.cr.commit()
        if not mk_log_id.log_line_ids:
            mk_log_id.unlink()
        else:
            return self.mk_instance_id.action_open_model_view(mk_log_id.ids, 'mk.log', 'Listing Update Logs')
        return True

    def prepare_vals_for_export_price_to_woocommerce(self, mk_listing_item_ids):
        update_variant_list = []
        for listing_item_id in mk_listing_item_ids:
            variant_vals = {}
            variant_price = self.mk_instance_id.pricelist_id.with_context(uom=listing_item_id.product_id.uom_id.id)._get_product_price(listing_item_id.product_id, quantity= 1.0, currency=False)
            variant_vals.update({'regular_price': str(variant_price), 'sale_price': str(variant_price), 'id': listing_item_id.mk_id})
            update_variant_list.append(variant_vals)
        return update_variant_list

    def woocommerce_update_product_price(self, mk_instance_ids):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
            mk_log_line_dict = {'error': [], 'success': []}
            listing_item_ids = self.get_mk_listing_item_for_price_update(mk_instance_id)
            if listing_item_ids:
                for listing_batch in tools.split_every(50, listing_item_ids, piece_maker=list):
                    listing_item_by_listing_dict = self.prepare_variant_data_by_product(listing_batch)
                    for mk_listing_id, mk_listing_item_ids in listing_item_by_listing_dict.items():
                        update_variant_list = mk_listing_id.prepare_vals_for_export_price_to_woocommerce(mk_listing_item_ids)

                        if mk_listing_item_ids and len(mk_listing_item_ids) == 1 and mk_listing_item_ids[0].mk_id == mk_listing_id.mk_id:
                            # This request sent when only template/listing in woocommerce
                            # This is only updating stock in the template/listing in woocommerce
                            variant_response, status_code = update_variant_list and mk_instance_id.fetch_data_from_woocommerce('post', "products/batch".format(mk_listing_id.mk_id),
                                                                                                                               data={'update': update_variant_list})
                        else:
                            # This request sent when when template with variants/listing items in woocommerce
                            # This is only updating stock in the variants/listing items in woocommerce
                            variant_response, status_code = update_variant_list and mk_instance_id.fetch_data_from_woocommerce('post',
                                                                                                                               "products/{}/variations/batch".format(mk_listing_id.mk_id),
                                                                                                                               data={'update': update_variant_list})

                        if status_code == 400:
                            ##Getting error code 400 and response have code 401: do not have access rights for the update product from Odoo to woo
                            error_dict = eval(variant_response.response.content)
                            log_message = f"UPDATE PRICE: Error while trying to export price for WooCommerce. \n Message : {error_dict.get('message', 'Permission issue')} \n Error : {error_dict.get('code', '')} {error_dict.get('data', '')}."
                            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                            return False
                        if status_code not in [200, 201]:
                            log_message = "UPDATE PRICE: Error while trying to export price for WooCommerce Product: {}, ERROR: {}.".format(mk_listing_id.name, variant_response)
                            mk_log_line_dict['error'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
                            continue
                        listing_item_response = variant_response[0].get('update')
                        if listing_item_response and listing_item_response[0].get('error', False):
                            log_message = "Error while trying to export price for WooCommerce Product: {}, ERROR: {}.".format(mk_listing_id.name,
                                                                                                                              listing_item_response[0].get('error').get('message'))
                            mk_log_line_dict['error'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
                            continue
                        log_message = "Successfully Updated Price of {} product in WooCommerce.".format(mk_listing_id.name)
                        mk_log_line_dict['success'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
                    self.env.cr.commit()
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
                if not mk_log_id.log_line_ids:
                    mk_log_id.unlink()
            mk_instance_id.write({'last_listing_price_update_date': fields.Datetime.now()})
        return True

    def cron_auto_update_woocommerce_product_price(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        self.woocommerce_update_product_price(mk_instance_id)
        return True
