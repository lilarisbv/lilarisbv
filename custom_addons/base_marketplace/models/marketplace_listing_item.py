import ast

from lxml import etree

from odoo import models, fields, api, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException

EXPORT_QTY_TYPE = [('fix', 'Fix'), ('percentage', 'Percentage')]


class MkListingItem(models.Model):
    _name = "mk.listing.item"
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Marketplace Listing Items'

    def _compute_sales_price_with_currency(self):
        for record in self:
            mk_instance_id = record.mk_instance_id or record.mk_listing_id.mk_instance_id
            record.sale_price = mk_instance_id.pricelist_id._get_product_price(record.product_id, 1.0, uom=record.product_id.uom_id)
            record.currency_id = mk_instance_id.pricelist_id.currency_id.id or False

    name = fields.Char('Name', required=True)
    sequence = fields.Integer(help="Determine the display order", default=10)
    mk_id = fields.Char("Marketplace Identification", copy=False)
    product_id = fields.Many2one('product.product', string='Product', ondelete='cascade')
    mk_listing_id = fields.Many2one('mk.listing', "Listing", ondelete="cascade")
    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')
    marketplace = fields.Selection(related="mk_instance_id.marketplace", string='Marketplace')
    default_code = fields.Char('Internal Reference')
    barcode = fields.Char('Barcode', copy=False, help="International Article Number used for product identification.")
    item_create_date = fields.Datetime("Creation Date", readonly=True, index=True)
    item_update_date = fields.Datetime("Updated On", readonly=True)
    is_listed = fields.Boolean("Listed?", copy=False)
    export_qty_type = fields.Selection(EXPORT_QTY_TYPE, string="Export Qty Type")
    export_qty_value = fields.Float("Export Qty Value")
    image_ids = fields.Many2many('mk.listing.image', 'mk_listing_image_listing_rel', 'listing_item_id', 'mk_listing_image_id', string="Images")
    sale_price = fields.Monetary(compute="_compute_sales_price_with_currency", currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', compute="_compute_sales_price_with_currency")

    @api.depends('name', 'default_code')
    def _compute_display_name(self):
        for record in self:
            if record.default_code:
                display_name = "[%s] %s" % (record.default_code, record.name)
            else:
                display_name = record.name
            record.display_name = display_name

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
        ret_val = super(MkListingItem, self).get_view(view_id=view_id, view_type=view_type, **options)
        doc = etree.XML(ret_val['arch'])

        if view_type == 'form':
            # Apply invisibility to pages and fields
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

    def create_or_update_pricelist_item(self, variant_price, update_product_price=False, reversal_convert=False, skip_conversion=False):
        self.ensure_one()
        instance_id = self.mk_instance_id or self.mk_listing_id.mk_instance_id
        pricelist_currency = instance_id.pricelist_id.currency_id
        company_currency = self.product_id.product_tmpl_id.currency_id
        if pricelist_currency != company_currency and not skip_conversion:
            if reversal_convert:
                variant_price = company_currency._convert(variant_price, pricelist_currency, instance_id.company_id, fields.Date.today())
            else:
                variant_price = pricelist_currency._convert(variant_price, company_currency, instance_id.company_id, fields.Date.today())
        pricelist_item_id = self.env['product.pricelist.item'].search([('pricelist_id', '=', instance_id.pricelist_id.id), ('product_id', '=', self.product_id.id)], limit=1)
        if not pricelist_item_id:
            instance_id.pricelist_id.sudo().write({'item_ids': [(0, 0, {
                'applied_on': '0_product_variant',
                'product_id': self.product_id.id,
                'product_tmpl_id': self.product_id.product_tmpl_id.id,
                'compute_price': 'fixed',
                'fixed_price': variant_price
            })]})
        elif pricelist_item_id and update_product_price:
            pricelist_item_id.sudo().write({'compute_price': 'fixed', 'fixed_price': variant_price})
        return True

    def action_change_listing_item_price(self):
        action = self.env.ref('base_marketplace.action_product_pricelistitem_mk').sudo().read()[0]
        custom_view_id = self.env.ref('base_marketplace.mk_product_pricelist_item_advanced_tree_view') if self.env.user.has_group('product.group_product_pricelist') else False
        if hasattr(self, '%s_action_change_price_view' % self.mk_instance_id.marketplace):
            custom_view_id = getattr(self, '%s_action_change_price_view' % self.mk_instance_id.marketplace)()
        context = self.env.context.copy()
        if 'context' in action and type(action['context']) == str:
            context.update(ast.literal_eval(action['context']))
        else:
            context.update(action.get('context', {}))
        action['context'] = context
        action['context'].update({
            'default_product_tmpl_id': self.product_id.product_tmpl_id.id,
            'default_product_id': self.product_id.id,
            'default_applied_on': '0_product_variant',
            'default_compute_price': 'fixed',
            'default_pricelist_id': self.mk_instance_id.pricelist_id.id,
            'search_default_Variant Rule': 1,
        })
        if custom_view_id:
            views = [(custom_view_id.id, 'list')]
            if self.env.user.has_group('product.group_product_pricelist'):
                views.append((self.env.ref('product.product_pricelist_item_form_view').id, 'form'))
            action['views'] = views
        instance_id = self.mk_instance_id or self.mk_listing_id.mk_instance_id
        action['domain'] = [('pricelist_id', '=', instance_id.pricelist_id.id), ('product_id', '=', self.product_id.id)]
        return action

    def action_open_listing_item_operation_view(self):
        """
        Task: T6152 - Add Stock & Price Update Functionality from Listing Items
        This method validates the selected listing items and opens the marketplace-specific update listing item operation view. The operation is allowed
        only for already exported (listed) listing items belonging to a single marketplace instance.
        Returns:
            dict: An Odoo window action dictionary to open the update wizard view.
        Raises:
            MarketplaceException: If the operation is not allowed due to invalid selection, unsupported marketplace, or instance constraints.
        """
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids')
        listing_items = self.env['mk.listing.item']
        if active_model == 'mk.listing.item' and active_ids:
            listing_items = self.env[active_model].browse(active_ids)
            listing_instance = listing_items.mapped('mk_instance_id')

            if len(listing_instance) == 1:
                if len(set(listing_items.mapped('is_listed'))) > 1:
                    raise MarketplaceException(_("Please ensure the selected items are already listed. Only listed items can be updated."), _("Operation not allowed!"))
                if listing_items[0].is_listed:
                    method_name = '%s_open_update_listing_item_view' % listing_instance.marketplace
                    if not hasattr(self, method_name):
                        raise MarketplaceException(_("This operation is not supported for the %s.") % listing_instance.marketplace, _("Operation not allowed!"))
                    return getattr(self, method_name)()
                else:
                    raise MarketplaceException(_("Please ensure the selected items are already listed. Only listed items can be updated."))

        if listing_items and listing_items[0].is_listed:
            action = self.sudo().env.ref('base_marketplace.action_update_listing_item_to_marketplace').read()[0]
        else:
            raise MarketplaceException(_("Please ensure the selected items are already listed. Only listed items can be updated."), _("Operation not allowed!"))
        context = self.env.context.copy()
        action['context'] = context
        return action
