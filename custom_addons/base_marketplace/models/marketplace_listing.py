from lxml import etree
from odoo import models, fields, api, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException


class MkListing(models.Model):
    _name = "mk.listing"
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Marketplace Listing'

    def _listing_item_count(self):
        for listing in self:
            listing.item_count = len(listing.listing_item_ids)

    name = fields.Char('Name', required=True)
    product_tmpl_id = fields.Many2one('product.template', 'Product Template', ondelete='cascade')
    product_category_id = fields.Many2one("product.category", "Product Category")
    listing_item_ids = fields.One2many("mk.listing.item", "mk_listing_id", "Listing Items")
    item_count = fields.Integer("Items", compute='_listing_item_count')
    mk_id = fields.Char("Marketplace Identification", copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')
    mk_instance_image = fields.Binary(related="mk_instance_id.image_small", string="Marketplace Image", help="Technical field to get the instance image for display purpose.",
                                      store=False)
    marketplace = fields.Selection(related="mk_instance_id.marketplace", string='Marketplace')
    listing_create_date = fields.Datetime("Creation Date", readonly=True, index=True)
    listing_update_date = fields.Datetime("Updated On", readonly=True)
    listing_publish_date = fields.Datetime("Published On", readonly=True)
    description = fields.Html('Description', sanitize_attributes=False)
    is_listed = fields.Boolean("Listed?", copy=False)
    is_published = fields.Boolean("Published", copy=False)
    image_ids = fields.One2many('mk.listing.image', 'mk_listing_id', 'Images')
    number_of_variants_in_mk = fields.Integer("Number of Variants in Marketplace.")

    def get_fields_for_hide(self):
        marketplace_list = self.env['mk.instance'].get_all_marketplace()
        field_dict = {}
        for marketplace in marketplace_list:
            if hasattr(self, '%s_hide_fields' % marketplace):
                field_list = getattr(self, '%s_hide_fields' % marketplace)()
                field_dict.update({marketplace: field_list})
        return field_dict

    def get_page_for_hide(self):
        marketplace_list = self.env['mk.instance'].get_all_marketplace()
        page_dict = {}
        for marketplace in marketplace_list:
            if hasattr(self, '%s_hide_page' % marketplace):
                page_list = getattr(self, '%s_hide_page' % marketplace)()
                page_dict.update({marketplace: page_list})
        return page_dict

    @api.model
    def get_view(self, view_id=None, view_type='form', **options):
        """
        Customizes the view to conditionally hide fields and pages based on the marketplace.
        """
        # Fetch the original view
        ret_val = super(MkListing, self).get_view(view_id=view_id, view_type=view_type, **options)
        doc = etree.XML(ret_val['arch'])

        if view_type == 'form':
            # Hide pages and fields for the specified marketplaces
            self._apply_invisible_to_pages(doc)
            self._apply_invisible_to_fields(doc)

        # Update the view architecture with the modified XML
        ret_val['arch'] = etree.tostring(doc, encoding='unicode')
        return ret_val

    def _apply_invisible_to_pages(self, doc):
        """
        Applies conditional invisibility to pages in the form view based on marketplace settings.
        Args:
            doc: The XML document (form view) to modify.
        """
        # Get the pages to hide based on marketplace
        need_to_hide_page_list = self.get_page_for_hide()

        for marketplace, page_list in need_to_hide_page_list.items():
            for page in page_list:
                for node in doc.xpath("//page[@name='%s']" % page):
                    self._set_invisibility(node, marketplace)

    def _apply_invisible_to_fields(self, doc):
        """
        Applies conditional invisibility to fields in the form view based on marketplace settings.
        Args:
            doc: The XML document (form view) to modify.
        """
        # Get the fields to hide based on marketplace
        need_to_hide_field_dict = self.get_fields_for_hide()

        for marketplace, field_list in need_to_hide_field_dict.items():
            for field in field_list:
                for node in doc.xpath("//field[@name='%s']" % field):
                    self._set_invisibility(node, marketplace)

    def _set_invisibility(self, node, marketplace):
        """
        Sets the 'invisible' attribute for a given node based on marketplace conditions.
        Args:
            node: The XML node (either a page or a field) to modify.
            marketplace (str): The marketplace condition for setting invisibility.
        """
        # Get the existing 'invisible' attribute
        existing_invisible = node.get("invisible", "")

        # Define the new condition based on the marketplace
        new_condition = f"marketplace == '{marketplace}'"

        # Combine the existing condition with the new condition, if applicable
        if existing_invisible:
            combined_condition = f"{existing_invisible} or {new_condition}"
            node.set("invisible", combined_condition)
        else:
            node.set("invisible", new_condition)

    def _marketplace_convert_weight(self, weight, weight_name, reverse=False):
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
        weight_uom_id = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
        if mk_uom_id:
            return weight_uom_id._compute_quantity(weight, mk_uom_id, round=False) if reverse else mk_uom_id._compute_quantity(weight, weight_uom_id, round=False)
        return False

    def get_odoo_product_variant_and_listing_item(self, mk_instance_id, variant_id, variant_barcode, variant_sku):
        """
        Finds the corresponding Odoo product variant and listing item for a marketplace instance, variant ID, barcode, and SKU.
        Args:
            mk_instance_id: The marketplace instance object.
            variant_id (str): The variant ID from the marketplace.
            variant_barcode (str): The barcode of the variant.
            variant_sku (str): The SKU of the variant.
        Returns:
            tuple: (odoo_product, listing_item) The Odoo product variant and listing item, if found.
        """
        mk_listing_item_obj, odoo_product_id = self.env['mk.listing.item'], False

        # Find the listing item by variant ID
        listing_item_id = self._search_listing_item_by_mk_id(mk_listing_item_obj, mk_instance_id, variant_id)

        # Handle barcode or SKU-based synchronization
        if mk_instance_id.sync_product_with == 'barcode':
            listing_item_id, odoo_product_id = self._sync_by_barcode(mk_instance_id, variant_barcode, variant_id, listing_item_id)
        elif mk_instance_id.sync_product_with == 'sku':
            listing_item_id, odoo_product_id = self._sync_by_sku(mk_instance_id, variant_sku, variant_id, listing_item_id)
        elif mk_instance_id.sync_product_with == 'barcode_or_sku':
            listing_item_id, odoo_product_id = self._sync_by_barcode_or_sku(mk_instance_id, variant_sku, variant_barcode, variant_id, listing_item_id)

        return odoo_product_id or listing_item_id.product_id, listing_item_id

    def _search_listing_item_by_mk_id(self, mk_listing_item_obj, mk_instance_id, variant_id):
        """Helper method to search for a listing item by variant ID."""
        return mk_listing_item_obj.search([('mk_id', '=', variant_id), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)

    def _sync_by_barcode(self, mk_instance_id, variant_barcode, variant_id, listing_item_id):
        """Handles product synchronization based on the barcode."""
        odoo_product_obj = self.env['product.product']

        if variant_barcode and not listing_item_id:
            listing_item_id = self.env['mk.listing.item'].search(
                [('product_id.barcode', '=', variant_barcode), '|', ('mk_id', '=', variant_id), ('mk_id', '=', False), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            if not listing_item_id:
                odoo_product_id = odoo_product_obj.search([('barcode', '=', variant_barcode)], limit=1)
                return listing_item_id, odoo_product_id
        return listing_item_id, False

    def _sync_by_sku(self, mk_instance_id, variant_sku, variant_id, listing_item_id):
        """Handles product synchronization based on the SKU."""
        odoo_product_obj = self.env['product.product']

        if variant_sku and not listing_item_id:
            listing_item_id = self.env['mk.listing.item'].search(
                ['|', ('default_code', '=ilike', variant_sku), ('product_id.default_code', '=ilike', variant_sku), '|', ('mk_id', '=', variant_id), ('mk_id', '=', False),
                 ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            if not listing_item_id:
                odoo_product_id = odoo_product_obj.search([('default_code', '=ilike', variant_sku)], limit=1)
                return listing_item_id, odoo_product_id
        return listing_item_id, False

    def _sync_by_barcode_or_sku(self, mk_instance_id, variant_sku, variant_barcode, variant_id, listing_item_id):
        """Handles product synchronization based on either barcode or SKU."""
        listing_item_id, odoo_product_id = self._sync_by_sku(mk_instance_id, variant_sku, variant_id, listing_item_id)
        if not listing_item_id and not odoo_product_id:
            listing_item_id, odoo_product_id = self._sync_by_barcode(mk_instance_id, variant_barcode, variant_id, listing_item_id)
        return listing_item_id, odoo_product_id

    def check_for_duplicate_sku_or_barcode_in_marketplace_product(self, sync_product_with, listing_item_validation_dict):
        """
        Main method to check for duplicate SKUs or Barcodes in the marketplace product.
        :param sync_product_with: Specifies whether the product should be synchronized by SKU, barcode, or both.
        :param listing_item_validation_dict: Dictionary containing product variants to validate.
        :return: (Boolean, String) -> Success flag and error message if applicable.
        """
        mk_sku_list, mk_barcode_list, error_message = self._extract_sku_and_barcode(sync_product_with, listing_item_validation_dict)
        if error_message:
            return False, error_message

        # Check duplicates in a marketplace product
        return self._validate_sku_or_barcode(sync_product_with, mk_sku_list, mk_barcode_list, listing_item_validation_dict)

    def _extract_sku_and_barcode(self, sync_product_with, listing_item_validation_dict):
        """
        Helper method to extract SKUs and Barcodes from product variants.
        :param sync_product_with: Specifies whether the product should be synchronized by SKU, barcode, or both.
        :param listing_item_validation_dict: Dictionary containing product variants.
        :return: (List, List) -> Two lists: one for SKUs and another for Barcodes.
        """
        mk_sku_list = []
        mk_barcode_list = []

        for mk_variant in listing_item_validation_dict.get('variants', []):
            if not mk_variant.get('sku') and not mk_variant.get('barcode'):
                return [], [], self._format_error_message("SKU and Barcode not set", listing_item_validation_dict)

            if sync_product_with == 'sku' and not mk_variant.get('sku', False):
                return [], [], self._format_error_message("SKU not set", listing_item_validation_dict)

            elif sync_product_with == 'barcode' and not mk_variant.get('barcode', False):
                return [], [], self._format_error_message("Barcode not set", listing_item_validation_dict)

            mk_variant.get('sku') and mk_sku_list.append(mk_variant.get('sku'))
            mk_variant.get('barcode') and mk_barcode_list.append(mk_variant.get('barcode'))

        return mk_sku_list, mk_barcode_list, False

    def _validate_sku_or_barcode(self, sync_product_with, mk_sku_list, mk_barcode_list, listing_item_validation_dict):
        """
        Validate SKU and Barcode lists and check for duplicates.
        :param sync_product_with: Specifies whether to sync by SKU, Barcode, or both.
        :param mk_sku_list: List of SKUs.
        :param mk_barcode_list: List of Barcodes.
        :param listing_item_validation_dict: Dictionary containing product details.
        :return: (Boolean, String) -> Success flag and error message if applicable.
        """
        count_unique_sku = len(set(mk_sku_list))
        count_unique_barcode = len(set(mk_barcode_list))

        # Check for unique Barcodes (mandatory as Odoo doesn't allow duplicate Barcodes)
        if mk_barcode_list and len(mk_barcode_list) != count_unique_barcode:
            return False, self._format_error_message("Duplicate Barcode found", listing_item_validation_dict)

        # Check for duplicate SKU or Barcode based on sync strategy
        if sync_product_with == 'sku' and len(mk_sku_list) != count_unique_sku:
            return False, self._format_error_message("Duplicate SKU found", listing_item_validation_dict)

        if sync_product_with == 'barcode' and len(mk_barcode_list) != count_unique_barcode:
            return False, self._format_error_message("Duplicate Barcode found", listing_item_validation_dict)

        if sync_product_with == 'barcode_or_sku':
            # If both lists are present, ensure at least one has unique values
            if (mk_barcode_list and len(mk_barcode_list) == count_unique_barcode) or (mk_sku_list and len(mk_sku_list) == count_unique_sku):
                return True, ""
            if len(mk_sku_list) != count_unique_sku:
                return False, self._format_error_message("Duplicate SKU found", listing_item_validation_dict)
            if len(mk_barcode_list) != count_unique_barcode:
                return False, self._format_error_message("Duplicate Barcode found", listing_item_validation_dict)

        return True, ""

    def _format_error_message(self, error_message, listing_item_validation_dict):
        """
        Helper method to format error messages.
        :param error_message: The error message to be formatted.
        :param listing_item_validation_dict: Dictionary containing product details.
        :return: String -> The formatted error message.
        """
        return "IMPORT LISTING: {} in Marketplace Product: {} and Marketplace Listing ID: {}".format(
            error_message, listing_item_validation_dict.get('name'), listing_item_validation_dict.get('id')
        )

    def check_validation_for_import_product(self, sync_product_with, listing_item_validation_dict, product_tmpl_id, existing_odoo_product, existing_mk_product):
        mk_sku_list = []
        mk_barcode_list = []
        for mk_variant in listing_item_validation_dict.get('variants'):
            variant_id = mk_variant.get('id')
            mk_variant.get('sku', False) and mk_sku_list.append(mk_variant.get('sku', False))
            mk_variant.get('barcode', False) and mk_barcode_list.append(mk_variant.get('barcode', False))
            barcode = mk_variant.get('barcode', False)
            listing_item_id = existing_mk_product.get(variant_id, False)
            odoo_product_id = existing_odoo_product.get(variant_id, False)

            # Looking for Odoo product having the same barcode to avoid duplication of Odoo product.
            if barcode:
                if not odoo_product_id and self.env['product.product'].search([('barcode', '=', barcode)]):
                    return False, f"IMPORT LISTING : ⚠️ DUPLICATE BARCODE DETECTED | Barcode: {barcode} | Marketplace Product: {listing_item_validation_dict.get('name')} | Marketplace ID: {listing_item_validation_dict.get('id')}"

                elif listing_item_id and self.env['product.product'].search([('barcode', '=', barcode), ('id', '!=', listing_item_id.product_id.id)]):
                    return False, f"IMPORT LISTING : ⚠️ DUPLICATE BARCODE DETECTED | Barcode: {barcode} | Marketplace Product: {listing_item_validation_dict.get('name')} | Marketplace ID: {listing_item_validation_dict.get('id')}"
        return True, ""

    def _find_odoo_product_from_marketplace_attribute(self, mk_attribute_dict, product_tmpl_id):
        domain = [('product_tmpl_id', '=', product_tmpl_id.id)]
        for name, value in mk_attribute_dict.items():
            attribute_id = self.env['product.attribute'].search([('name', '=ilike', name)], limit=1)
            attribute_value_id = self.env['product.attribute.value'].search([('attribute_id', '=', attribute_id.id), ('name', '=ilike', value)], limit=1)
            if attribute_value_id:
                ptav_id = self.env['product.template.attribute.value'].search([
                    ('product_attribute_value_id', '=', attribute_value_id.id), ('attribute_id', '=', attribute_id.id), ('product_tmpl_id', '=', product_tmpl_id.id)], limit=1)
                if ptav_id:
                    domain.append(('product_template_attribute_value_ids', '=', ptav_id.id))
        return self.env['product.product'].search(domain)

    def open_listing_in_marketplace(self):
        self.ensure_one()
        if hasattr(self, '%s_open_listing_in_marketplace' % self.marketplace):
            url = getattr(self, '%s_open_listing_in_marketplace' % self.marketplace)()
            if url:
                client_action = {
                    'type': 'ir.actions.act_url',
                    'name': "Marketplace URL",
                    'target': 'new',
                    'url': url,
                }
                return client_action
        marketplace_values = dict(self.mk_instance_id._fields['marketplace'].selection)
        raise MarketplaceException(_(f"Navigation not available for {marketplace_values.get(self.mk_instance_id.marketplace)} marketplace."))

    def marketplace_published(self):
        if hasattr(self, '%s_published' % self.marketplace):
            getattr(self, '%s_published' % self.marketplace)()
        return True

    def action_open_listing_operation_view(self):
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids')
        if active_model == 'mk.listing' and active_ids:
            listing = self.env[active_model].browse(active_ids)
            listing_instance = listing.mapped('mk_instance_id')
            # making sure we only open marketplace wise view only if selected listing belongs to single instance.
            if len(listing_instance) == 1:
                if len(set(listing.mapped('is_listed'))) > 1:
                    raise MarketplaceException(
                        "Please ensure that the selected listings are intended for either updating or exporting only. Do not mix exported listings and not exported listings.",
                        "Operation not allowed!")
                if listing[0].is_listed:
                    if hasattr(self, '%s_open_update_listing_view' % listing_instance.marketplace):
                        return getattr(self, '%s_open_update_listing_view' % listing_instance.marketplace)()
                else:
                    if hasattr(self, '%s_open_export_listing_view' % listing_instance.marketplace):
                        return getattr(self, '%s_open_export_listing_view' % listing_instance.marketplace)()
        if listing[0].is_listed:
            action = self.sudo().env.ref('base_marketplace.action_product_export_to_marketplace').read()[0]
        else:
            action = self.sudo().env.ref('base_marketplace.action_listing_update_to_marketplace').read()[0]
        context = self.env.context.copy()
        action['context'] = context
        return action

    def get_mk_listing_item(self, mk_instance_id):
        if mk_instance_id.last_stock_update_date:
            where_clause = "sm.write_date >= %s AND "
        else:
            return self.env['mk.listing.item'].search([('mk_instance_id', '=', mk_instance_id.id), ('is_listed', '=', True)])

        query = """
                SELECT mkli.id
                FROM stock_move sm
                JOIN mk_listing_item mkli ON sm.product_id = mkli.product_id 
                    AND mkli.is_listed = TRUE 
                    AND mkli.mk_instance_id = %s
                WHERE {} state IN ('partially_available', 'assigned', 'done', 'cancel') 
                    AND sm.company_id = %s
        """.format(where_clause)
        self.env.cr.execute(query, tuple([mk_instance_id.id, mk_instance_id.last_stock_update_date, mk_instance_id.company_id.id]))
        result = [int(i[0]) for i in self.env.cr.fetchall()]

        mrp = mk_instance_id._is_module_installed('mrp')
        if mrp:
            query = """
            SELECT mkli.id
            FROM (
                SELECT sm.product_id
                FROM stock_move AS sm
                WHERE {} sm.company_id = %s
                  AND sm.state IN ('partially_available', 'assigned', 'done', 'cancel')
                GROUP BY sm.product_id
            ) AS filtered_moves
            JOIN mrp_bom_line AS mbl ON filtered_moves.product_id = mbl.product_id
            JOIN mrp_bom AS mbom ON mbl.bom_id = mbom.id
            JOIN product_product AS pp ON mbom.product_tmpl_id = pp.product_tmpl_id
            JOIN mk_listing_item AS mkli ON pp.id = mkli.product_id AND mkli.is_listed = true AND mkli.mk_instance_id = %s
            GROUP BY mkli.id;
            """.format(where_clause)
            params = [mk_instance_id.last_stock_update_date, mk_instance_id.company_id.id, mk_instance_id.id] if mk_instance_id.last_stock_update_date else [mk_instance_id.company_id.id, mk_instance_id.id]
            self.env.cr.execute(query, tuple(params))
            result += [int(i[0]) for i in self.env.cr.fetchall()]
        return list(set(result))

    def get_mk_listing_item_from_product_variants(self, product_ids, mk_instance_id):
        listing_items = self.env['mk.listing.item'].search([('product_id', 'in', product_ids.ids), ('is_listed', '=', True), ('mk_instance_id', '=', mk_instance_id.id)])
        return listing_items

    def get_mk_listing_item_for_price_update(self, mk_instance_id):
        if mk_instance_id.last_listing_price_update_date:
            pricelist_items = mk_instance_id.pricelist_id.item_ids.filtered(lambda x: x.write_date > mk_instance_id.last_listing_price_update_date and x.applied_on == '0_product_variant')
        else:
            pricelist_items = mk_instance_id.pricelist_id.item_ids.filtered(lambda x: x.applied_on == '0_product_variant')
        listing_items = self.get_mk_listing_item_from_product_variants(pricelist_items.mapped('product_id'), mk_instance_id)
        return listing_items

    def remove_extra_listing_item(self, mk_id_list):
        if mk_id_list:
            mk_log_id = self.env.context.get('mk_log_id', False)
            queue_line_id = self.env.context.get('queue_line_id', False)
            odoo_variant_id_list = [item_id.mk_id for item_id in self.listing_item_ids]
            need_to_remove_id_set = set(mk_id_list) ^ set(odoo_variant_id_list)
            if need_to_remove_id_set:
                need_to_remove_id_list = list(need_to_remove_id_set)
                self.env['mk.listing.item'].search([('mk_id', 'in', need_to_remove_id_list)]).unlink()
                self.env['mk.log'].create_update_log(mk_instance_id=self.mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict={'success': [
                    {'log_message': 'IMPORT LISTING: {} listing item deleted.'.format(need_to_remove_id_list), 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        return True

    def action_open_listing_view(self):
        if not self.product_tmpl_id:
            return True
        action = {'res_model': 'mk.listing', 'type': 'ir.actions.act_window', 'target': 'current', 'view_mode': 'form', 'res_id': self.id}
        return action

    def _product_exists_in_odoo(self, variant_sku):
        """
        Check if the product with the given SKU exists in Odoo.
        Args:
            variant_sku (str): The product SKU.
        Returns:
            bool: object of product.product
        """
        if not variant_sku:
            return False
        return self.env['product.product'].search([('default_code', '=ilike', variant_sku)], limit=1)