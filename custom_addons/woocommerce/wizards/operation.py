from datetime import timedelta
from odoo import models, fields, _
from odoo.exceptions import UserError

WOOCOMMERCE_OPERATIONS = [('import', 'Import'),
                          ('export', 'Export')]

WOOCOMMERCE_IMPORT_OPERATIONS = [('import_customers', 'Import Customers'), ('import_listings', 'Import Listings'), ('import_orders', 'Import Orders'),
                                 ('import_stock', 'Import Inventory'), ('import_categories', 'Import Categories'), ('import_tags', 'Import Tags')]

WOOCOMMERCE_EXPORT_OPERATIONS = [('export_listings', 'Export Listings to WooCommerce'), ('update_listings', 'Update Listings to WooCommerce'),
                                 ('update_prices', 'Update Prices to WooCommerce'), ('update_stock', 'Update Inventory to WooCommerce'),
                                 ('update_images', 'Update Images to WooCommerce'), ('update_order_status', 'Update Order Status to WooCommerce')]

WOOCOMMERCE_PRODUCT_STATUS = [('any', 'Any'), ('draft', 'Draft'), ('pending', 'Pending'), ('private', 'Private'), ('publish', 'Publish')]


class MarketplaceOperation(models.TransientModel):
    _inherit = "mk.operation"

    def _get_default_woo_listing_from_date(self):
        mk_instance_id = self.env.context.get('active_id')
        mk_instance_id = self.env['mk.instance'].search([('id', '=', mk_instance_id)], limit=1)
        from_date = mk_instance_id.last_listing_import_date if mk_instance_id.last_listing_import_date else fields.Datetime.now() - timedelta(days=30)
        from_date = fields.Datetime.to_string(from_date)
        return from_date

    def _get_default_woo_listing_to_date(self):
        to_date = fields.Datetime.now()
        to_date = fields.Datetime.to_string(to_date)
        return to_date

    woocommerce_operations = fields.Selection(WOOCOMMERCE_OPERATIONS, string="WooCommerce Operation", default='import')

    # Marketplace Import Fields
    woocommerce_import_operations = fields.Selection(WOOCOMMERCE_IMPORT_OPERATIONS, string="WooCommerce Import Operation", default='import_customers')
    import_categories = fields.Boolean("Import Categories")
    create_odoo_categories = fields.Boolean("Create Odoo Categories?")
    import_tags = fields.Boolean("Import Tags")
    publish_in_store = fields.Selection([('publish', 'Published'), ('draft', 'Unpublished')], string="Publish In Store?")
    woocommerce_from_listing_date = fields.Datetime("WooCommerce From Listing Date", default=_get_default_woo_listing_from_date)
    woocommerce_to_listing_date = fields.Datetime("WooCommerce To Listing Date", default=_get_default_woo_listing_to_date)
    woocommerce_product_status = fields.Selection(WOOCOMMERCE_PRODUCT_STATUS, string="Status", default="any")
    woo_update_image = fields.Boolean("Update Image?", help="If True than it will update Image in product template and its Variants.")

    # Marketplace Import Fields
    woocommerce_export_operations = fields.Selection(WOOCOMMERCE_EXPORT_OPERATIONS, string="WooCommerce Export Operation", default='export_listings')

    def do_woocommerce_operations(self):
        instance = self.mk_instance_id
        if not instance:
            raise UserError(_("Please select marketplace instance to process."))

        if self.woocommerce_operations == 'import':
            if self.woocommerce_import_operations == 'import_customers':
                self.env['res.partner'].woocommerce_import_customers(instance)
            if self.woocommerce_import_operations == 'import_listings':
                woo_from_listing_date = self.woocommerce_from_listing_date and self.mk_instance_id.woo_convert_date_onstore_tz(self.woocommerce_from_listing_date) or self.woocommerce_from_listing_date
                woo_to_listing_date = self.woocommerce_to_listing_date and self.mk_instance_id.woo_convert_date_onstore_tz(self.woocommerce_to_listing_date) or self.woocommerce_to_listing_date
                action = self.env['mk.listing'].woocommerce_import_listings(instance, from_listing_date=woo_from_listing_date, to_listing_date=woo_to_listing_date, mk_listing_id=self.mk_listing_id, update_product_price=self.update_product_price,
                                                                   woo_update_image=self.woo_update_image,update_existing_product=self.update_existing_product, status=self.woocommerce_product_status)
                if action and type(action) == dict:
                    return action
            if self.woocommerce_import_operations == 'import_stock':
                self.env['mk.listing'].woocommerce_import_stock(instance)
            if self.woocommerce_import_operations == 'import_orders':
                action = self.env['sale.order'].woocommerce_import_orders(instance, self.from_date, self.to_date, mk_order_id=self.mk_order_id)
                if action and type(action) == dict:
                    return action
            if self.woocommerce_import_operations == 'import_categories':
                self.env['woo.product.category.ts'].with_context(create_odoo_categories=self.create_odoo_categories).import_woocommerce_categories(self.mk_instance_id)
            if self.woocommerce_import_operations == 'import_tags':
                self.env['woo.product.tags.ts'].import_woocommerce_tags(self.mk_instance_id)
        else:
            if self.woocommerce_export_operations == 'export_listings':
                self.is_update_product, self.is_set_price, self.is_set_images, self.is_set_quantity = True, True, True, True
                return self.export_listing_to_mk()
            if self.woocommerce_export_operations == 'update_listings':
                self.is_update_product = True
                return self.update_listing_to_mk()
            if self.woocommerce_export_operations == 'update_prices':
                self.is_set_price = True
                self.update_listing_to_mk()
            if self.woocommerce_export_operations == 'update_stock':
                self.is_set_quantity = True
                self.update_listing_to_mk()
            if self.woocommerce_export_operations == 'update_images':
                self.is_set_images = True
                self.update_listing_to_mk()
            if self.woocommerce_export_operations == 'update_order_status':
                self.env['sale.order'].woocommerce_update_order_status(instance)
