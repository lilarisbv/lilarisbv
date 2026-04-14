from datetime import timedelta

from odoo.exceptions import UserError

from odoo import api, models, fields, tools, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException


class MarketplaceOperation(models.TransientModel):
    _name = "mk.operation"
    _description = "Marketplace Operation"

    def _get_default_from_date(self):
        mk_instance_id = self.env.context.get('active_id')
        mk_instance_id = self.env['mk.instance'].search([('id', '=', mk_instance_id)], limit=1)
        from_date = mk_instance_id.last_order_sync_date if mk_instance_id.last_order_sync_date else fields.Datetime.now() - timedelta(3)
        from_date = fields.Datetime.to_string(from_date)
        return from_date

    def _get_default_to_date(self):
        to_date = fields.Datetime.now()
        to_date = fields.Datetime.to_string(to_date)
        return to_date

    def _get_default_marketplace(self):
        if 'default_marketplace_instance_id' in self.env.context:
            instance_id = self.env.context.get('active_id', False)
        else:
            instance_id = self.env['mk.instance'].search([('state', '=', 'confirmed')], limit=1).id
        return instance_id

    def _get_default_marketplaces(self):
        if 'default_marketplace_instance_id' in self.env.context:
            instance_ids = self.env.context.get('active_ids', False)
        else:
            instance_ids = self.env['mk.instance'].search([('state', '=', 'confirmed')]).ids
        return instance_ids if len(instance_ids) == 1 else False

    mk_instance_ids = fields.Many2many("mk.instance", 'mk_instance_operation_rel', 'instance_id', 'operation_id', string="Marketplaces", domain=[('state', '=', 'confirmed')],
                                       default=_get_default_marketplaces)
    mk_instance_id = fields.Many2one("mk.instance", string="Marketplace", domain=[('state', '=', 'confirmed')], default=_get_default_marketplace)
    marketplace = fields.Selection(related="mk_instance_id.marketplace", string='Marketplace Name')

    # Marketplace Import Fields
    import_customers = fields.Boolean("Import Customers")
    import_products = fields.Boolean("Import Listing")
    is_create_product = fields.Boolean("Auto Create Product?", help="create a new Odoo product if not found.")
    update_existing_product = fields.Boolean("Update Existing Listing?", help="If True than it will update listing and listing item.")
    update_product_price = fields.Boolean("Update Price?", help="If True than it will update price in instance's pricelist.")
    import_stock = fields.Boolean("Import Stock")
    import_orders = fields.Boolean("Import Sale Orders")
    from_date = fields.Datetime("From Date", default=_get_default_from_date)
    to_date = fields.Datetime("To Date", default=_get_default_to_date)
    mk_listing_id = fields.Char("Marketplace Listing ID", help="Used to import specific Product from Marketplace using Marketplace ID.")
    mk_order_id = fields.Char("Marketplace Order ID", help="Used to import specific Order from Marketplace using Marketplace ID.")

    # Marketplace Export Fields
    is_update_order_status = fields.Boolean("Update Order Status")
    is_set_price = fields.Boolean("Set Price?")
    is_set_quantity = fields.Boolean("Set Quantity?")
    is_update_product = fields.Boolean("Update Detail?", default=True)
    is_publish_in_store = fields.Boolean("Publish in Store?")
    is_set_images = fields.Boolean("Set Images?")
    is_export_products = fields.Boolean("Export Products?")
    is_update_products = fields.Boolean("Update Products?")

    restricted_operation = fields.Boolean("Restricted Operation?", help="Technical field to hide process button in some use case.")
    cron_message = fields.Char("Cron/Schedule Action Message")

    @api.model
    def default_get(self, default_fields):
        res = super(MarketplaceOperation, self).default_get(default_fields)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids')
        if active_model == 'mk.listing' and active_ids:
            listing = self.env[active_model].browse(active_ids)
            if len(listing.mapped('mk_instance_id')) > 1:
                raise UserError(_('Operation not allowed! Make sure selected listing belongs to only one instance'))
        return res

    def do_import_operations(self):
        if not self.mk_instance_id:
            raise UserError(_("Please select marketplace instance to process."))
        instance = self.mk_instance_id
        if self.import_customers:
            if hasattr(self.env['res.partner'], '%s_import_customers' % instance.marketplace):
                getattr(self.env['res.partner'], '%s_import_customers' % instance.marketplace)(instance)
        if self.import_products:
            if hasattr(self.env['mk.listing'], '%s_import_listings' % instance.marketplace):
                getattr(self.env['mk.listing'], '%s_import_listings' % instance.marketplace)(instance, mk_listing_id=self.mk_listing_id, update_product_price=self.update_product_price,
                                                                                             update_existing_product=self.update_existing_product)
        if self.import_orders:
            if hasattr(self.env['sale.order'], '%s_import_orders' % instance.marketplace):
                getattr(self.env['sale.order'].with_context(from_import_screen=True), '%s_import_orders' % instance.marketplace)(instance, self.from_date, self.to_date,
                                                                                                                                 mk_order_id=self.mk_order_id)
        if self.import_stock:
            if hasattr(self.env['mk.listing'], '%s_import_stock' % instance.marketplace):
                getattr(self.env['mk.listing'], '%s_import_stock' % instance.marketplace)(instance)

    def do_export_operations(self):
        if not self.mk_instance_id:
            raise UserError(_("Please select marketplace instance to process."))
        instance = self.mk_instance_id
        if self.is_export_products:
            self.export_listing_to_mk()
        if self.is_update_products:
            self.update_listing_to_mk()
        if self.is_set_price:
            if hasattr(self.env['mk.listing'], '%s_set_price' % instance.marketplace):
                getattr(self.env['mk.listing'], '%s_set_price' % instance.marketplace)(instance)
        if self.is_set_quantity:
            if hasattr(self.env['mk.listing'], '%s_set_quantity' % instance.marketplace):
                getattr(self.env['mk.listing'], '%s_set_quantity' % instance.marketplace)(instance)
        if self.is_set_images:
            if hasattr(self.env['mk.listing'], '%s_set_images' % instance.marketplace):
                getattr(self.env['mk.listing'], '%s_set_images' % instance.marketplace)(instance)
        if self.is_update_order_status:
            if hasattr(self.env['sale.order'], '%s_update_order_status' % instance.marketplace):
                getattr(self.env['sale.order'], '%s_update_order_status' % instance.marketplace)(instance)

    def mk_add_to_listing(self):
        self.ensure_one()
        product_template_obj = self.env['product.template']
        product_active_ids = self.env.context.get('active_ids', [])
        odoo_template_ids = product_template_obj.search([('id', 'in', product_active_ids)])
        if not odoo_template_ids:
            raise UserError(_("Something went wrong! Odoo isn't able to identify selected products or please select product again."))
        listing_ids = self.env['mk.listing']
        for instance in self.mk_instance_ids:
            for product_template in odoo_template_ids:
                if not product_template._check_barcode_and_sku(instance):
                    raise UserError(_("Product : {} Internal Reference (SKU) or Barcode is missing in some product(s)! \n\nPlease set the unique internal reference or Barcode in all variants as per your Instance Configuration.".format(product_template.name)))
                listing_ids |= product_template.create_or_update_listing(instance)
        if listing_ids:
            action = self.sudo().env.ref('base_marketplace.action_marketplace_listing_all').read()[0]
            action['domain'] = [('id', 'in', listing_ids.ids)]
            return action
        return True

    def export_listing_to_mk(self):
        self.ensure_one()
        mk_listing_obj = self.env['mk.listing']
        if self.env.context.get('active_model') == 'mk.listing' and self.env.context.get('active_ids', []):
            listing_to_export = mk_listing_obj.search([('id', 'in', self.env.context.get('active_ids', [])), ('is_listed', '=', False)])
        else:
            listing_to_export = mk_listing_obj.search([('mk_instance_id', '=', self.mk_instance_id.id), ('is_listed', '=', False)])
        if not listing_to_export:
            raise UserError(_("Could not find any listing for export."))
        if len(listing_to_export.mapped('mk_instance_id')) > 1:
            raise UserError(_("Operation not allowed! Make sure selected listing belongs to only one instance."))
        instance_id = listing_to_export[0].mk_instance_id
        if listing_to_export[0].mk_instance_id.state != 'confirmed':
            raise UserError(_("You can export a listing only with a confirmed instance. Please ensure the instance is confirmed before exporting the listing."))
        if hasattr(listing_to_export, '%s_export_product_limit' % instance_id.marketplace):
            getattr(listing_to_export, '%s_export_product_limit' % instance_id.marketplace)()
        if hasattr(listing_to_export, '%s_export_listing_to_mk' % instance_id.marketplace):
            return getattr(listing_to_export, '%s_export_listing_to_mk' % instance_id.marketplace)(self)
        return True

    def update_listing_to_mk(self):
        self.ensure_one()
        mk_listing_obj = self.env['mk.listing']
        if self.env.context.get('active_model') == 'mk.listing' and self.env.context.get('active_ids', []):
            listing_to_update = mk_listing_obj.search([('id', 'in', self.env.context.get('active_ids', [])), ('is_listed', '=', True)])
        else:
            listing_to_update = mk_listing_obj.search([('mk_instance_id', '=', self.mk_instance_id.id), ('is_listed', '=', True)])
        if not listing_to_update:
            raise UserError(_("Could not find any listing for update."))
        if len(listing_to_update.mapped('mk_instance_id')) > 1:
            raise UserError(_("Operation not allowed! Make sure selected listing belongs to only one instance."))
        if listing_to_update[0].mk_instance_id.state != 'confirmed':
            raise UserError(_("You can update a listing only with a confirmed instance. Please ensure the instance is confirmed before updating the listing."))
        mk_instance_id = listing_to_update.mk_instance_id
        if hasattr(listing_to_update, '%s_update_listing_to_mk' % mk_instance_id.marketplace):
            return getattr(listing_to_update, '%s_update_listing_to_mk' % mk_instance_id.marketplace)(self)
        return True

    def update_listing_item_to_mk(self):
        """
        Task: T6152 - Add Stock & Price Update Functionality from Listing Items
        This method updates listing items on the marketplace. It validates the selected listing items and ensures that the
        update operation is executed only when all required conditions are met.
        Returns:
            Result returned by the marketplace-specific update listing item implementation.
        Raises:
            MarketplaceException: If validation fails or the marketplace does not support updating listing items.
        """
        self.ensure_one()
        mk_listing_item_obj = self.env['mk.listing.item']
        if self.env.context.get('active_model') == 'mk.listing.item' and self.env.context.get('active_ids', []):
            listing_items_to_update = mk_listing_item_obj.search([('id', 'in', self.env.context.get('active_ids', [])), ('is_listed', '=', True)])
        else:
            listing_items_to_update = mk_listing_item_obj.search([('mk_instance_id', '=', self.mk_instance_id.id), ('is_listed', '=', True)])
        if not listing_items_to_update:
            raise MarketplaceException("Could not find any listing items for update.")
        if len(listing_items_to_update.mapped('mk_instance_id')) > 1:
            raise MarketplaceException("Operation not allowed! Make sure selected listing items belongs to only one instance.")
        if listing_items_to_update[0].mk_instance_id.state != 'confirmed':
            raise MarketplaceException("You can update a listing items only with a confirmed instance. Please ensure the instance is confirmed before updating the listing items.")
        mk_instance_id = listing_items_to_update.mk_instance_id
        method_name = '%s_update_listing_item_to_mk' % mk_instance_id.marketplace
        if not hasattr(listing_items_to_update, method_name):
            raise MarketplaceException(f"This operation is not supported for the {mk_instance_id.marketplace}.", "Operation not allowed!")
        return getattr(listing_items_to_update, method_name)(self)

    def _get_active_cron_operation_wise(self):
        if hasattr(self, '%s_get_active_cron_operation_wise' % self.mk_instance_id.marketplace):
            return getattr(self, '%s_get_active_cron_operation_wise' % self.mk_instance_id.marketplace)()
        return {}

    def _check_process_cron_job(self, cron):
        with self.pool.cursor() as new_cr:
            self = self.with_env(self.env(cr=new_cr))
            with tools.mute_logger('odoo.sql_db'):
                self.env.cr.execute("SELECT id FROM ir_cron WHERE id = %s FOR UPDATE NOWAIT", (cron.id,))
            self.env.cr.rollback()
        difference = cron.nextcall - fields.Datetime.now()
        diff_days = difference.days
        if not diff_days < 0 and cron.active:
            days = diff_days * 1440 if diff_days > 0 else 0
            minutes = int(difference.seconds / 60) + days
            self.cron_message = _("Selected operation is scheduled to be executed by an automated scheduler, and the next scheduled run for this process will occur in {} minute(s).".format(minutes))
        return True

    def do_check_cron_status(self, operation_type, operation):
        cron_dict = self._get_active_cron_operation_wise()
        self.restricted_operation = False
        self.cron_message = False
        if self.marketplace in cron_dict:
            if operation_type in cron_dict[self.marketplace]:
                operation_cron_dict = cron_dict[self.marketplace][operation_type]
                if operation in operation_cron_dict:
                    for cron in operation_cron_dict[operation]:
                        try:
                            self._check_process_cron_job(cron)
                        except Exception:
                            self.restricted_operation = True
                            self.cron_message = _("Restricted! The execution of this cron task is in progress. Proceeding with this action may result in duplicate records.")
                            return False
