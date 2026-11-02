from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.addons.bol.models.marketplace_listing import BOL_FULFILMENT_METHOD

FBR_OPERATIONS = [('import_order', 'Import Orders'), ('update_order', 'Update Tracking Details'), ('export_stock', 'Export Stock From Odoo to Bol')]
FBB_OPERATIONS = [('import_order', 'Import Orders'), ('import_stock', 'Import FBB Stock')]
BOL_OPERATIONS = [('import_product', 'Sync Offer/Products'), ('import_order_by_ids', 'Import Order By ids'), ('import_shipments', 'Import Shipments'),
                  ('import_return', 'Import Return'), ('import_shipped_order', 'Import Shipped Order'), ('import_invoice_request', 'Import Invoice Request'), ('export_price', 'Export Prices to Bol'),('export_invoice', 'Export Invoice to Bol'),
                  # ('map_product', 'Map Offers Manually Via CSV File')
                  ]


class MarketplaceOperation(models.TransientModel):
    _inherit = "mk.operation"

    def _get_default_bol_return_import_from_date(self):
        mk_instance_id = self.env.context.get('active_id')
        mk_instance_id = self.env['mk.instance'].search([('id', '=', mk_instance_id)], limit=1)
        from_date = mk_instance_id.bol_last_return_sync_on if mk_instance_id.bol_last_return_sync_on else fields.Datetime.now() - timedelta(3)
        from_date = fields.Datetime.to_string(from_date)
        return from_date

    @api.depends('mk_instance_ids')
    def _compute_bol_show_fulfillment_type(self):
        for record in self:
            show_fulfillment_type = False
            if any([instance.marketplace == 'bol' and instance.bol_operation_type == 'Both' for instance in self.mk_instance_ids]):
                show_fulfillment_type = True
            record.bol_show_fulfillment_type = show_fulfillment_type


    bol_operation_type = fields.Selection([('FBR', 'FBR'), ('FBB', 'FBB'), ('Both', 'FBR & FBB')], default="FBR", string="Operation For")
    bol_fbr_operations = fields.Selection(FBR_OPERATIONS, string="FBR Operations", default='import_order')
    bol_fbb_operations = fields.Selection(FBB_OPERATIONS, string="FBB Operations", default='import_order')
    bol_operations = fields.Selection(BOL_OPERATIONS, string="Operation", default='import_product')
    bol_validate_inventory_adjustment = fields.Boolean("Auto Validate Inventory Adjustment?")
    bol_return_import_from_date = fields.Datetime("Return From Date", default=_get_default_bol_return_import_from_date)

    # Add to Listing fields
    bol_show_fulfillment_type = fields.Boolean(compute="_compute_bol_show_fulfillment_type", string="Show Bol Fulfillment Type?",
                                               help="Technical field to visible Fulfillment option based on allowed fulfillment option defined in instance while adding product to listing.")
    bol_fulfilment_method = fields.Selection(BOL_FULFILMENT_METHOD, string='Fulfilment Method', default='FBR', help='Specifies whether this shipment has been fulfilled by the retailer (FBR) or fulfilled by bol.com (FBB). Defaults to FBR.')

    # Update Offer
    bol_publish_in_store = fields.Selection([('publish', 'Published'), ('hold', 'Unpublished')],
                                            string="Publish In Bol.com?",
                                            help='Published: The offer becomes visible to customers and is available for purchase.\n'
                                                 'Unpublished: The offer becomes invisible to customers and is not available for purchase.\n')

    @api.onchange("bol_publish_in_store")
    def onchange_bol_publish_in_store(self):
        if not self.mk_instance_id:
            return
        if self.marketplace == 'bol':
            if self.bol_publish_in_store and not self.is_update_product:
                self.is_update_product = True

    def mk_add_to_listing(self):
        return super(MarketplaceOperation, self.with_context(bol_fulfilment_method=self.bol_fulfilment_method)).mk_add_to_listing()

    @api.onchange("bol_operation_type", "bol_fbr_operations", "bol_fbb_operations", "bol_operations")
    def onchange_bol_operations(self):
        if not self.mk_instance_id:
            return
        if self.marketplace == 'bol':
            selected_operation = False
            if self.bol_operation_type == 'FBR':
                selected_operation = self.bol_fbr_operations
            elif self.bol_operation_type == 'FBB':
                selected_operation = self.bol_fbb_operations
            else:
                selected_operation = self.bol_operations
            self.do_check_cron_status(self.bol_operation_type, selected_operation)

    def export_listing_to_mk(self):
        self.ensure_one()
        if self.mk_instance_id.marketplace != 'bol':
            return super().export_listing_to_mk()
        mk_listing_obj = self.env['mk.listing']
        if self._context.get('active_model') == 'mk.listing' and self._context.get('active_ids'):
            listings = mk_listing_obj.search([('id', 'in', self._context.get('active_ids', [])), ('is_listed', '=', False)])
        else:
            listings = mk_listing_obj.search([('mk_instance_id', '=', self.mk_instance_id.id), ('is_listed', '=', False)])
        if not listings:
            raise UserError(_("Could not find any listing for export."))
        if len(listings.mapped('mk_instance_id')) > 1:
            raise UserError(_("Operation not allowed! Make sure selected listings belong to only one instance."))
        instance = listings[0].mk_instance_id
        if instance.state != 'confirmed':
            raise UserError(_("You can export a listing only with a confirmed instance. Please ensure the instance is confirmed before exporting the listing."))
        return listings.bol_prepare_export_queue(self)

    def do_bol_operations(self):
        instance = self.mk_instance_id
        if not instance:
            raise MarketplaceException(_("Please select marketplace instance to process."))

        if self.bol_operation_type == 'FBR':
            action = self.handle_bol_fbr_operations(instance)
        elif self.bol_operation_type == 'FBB':
            action = self.handle_bol_fbb_operations(instance)
        else:
            action = self.handle_bol_both_operations(instance)
        return action

    def handle_bol_fbr_operations(self, instance):
        action = True
        if self.bol_fbr_operations == 'import_order':
            action = self.env['sale.order'].bol_import_orders(instance)
        elif self.bol_fbr_operations == 'update_order':
            action = self.env['sale.order'].bol_update_order_status(instance)
        elif self.bol_fbr_operations == 'export_stock':
            action = self.env['mk.listing'].update_stock_in_bol_ts(instance)
        if action and type(action) == dict:
            return action
        return action

    def handle_bol_fbb_operations(self, instance):
        action = True
        if self.bol_fbb_operations == 'import_order':
            if not instance.bol_fbb_warehouse_id:
                raise MarketplaceException(_("Please define FBB warehouse in selected Bol instance >> Configuration >> FBB Warehouse and try again!"))
            if not instance.bol_fbb_workflow_id:
                raise MarketplaceException(_("Please define FBB order workflow in selected Bol instance >> Workflow >> FBB Workflow and try again!"))
            action = self.env['sale.order'].bol_import_orders(instance, type="FBB")
        elif self.bol_fbb_operations == 'import_stock':
            action = self.env['mk.listing'].bol_import_fbb_stock(instance, auto_validate=self.bol_validate_inventory_adjustment)
        if action and type(action) == dict:
            return action
        return action

    def handle_bol_both_operations(self, instance):
        action = True
        if self.bol_operations == 'import_product':
            action = self.env['mk.listing'].bol_import_listings(instance, mk_listing_id=self.mk_listing_id, update_product_price=self.update_product_price, update_existing_product=self.update_existing_product)
        elif self.bol_operations == 'import_order_by_ids':
            order_ids = [order_id.strip() for order_id in self.mk_order_id.split(',')]
            action = self.env['sale.order'].bol_import_order_by_ids(instance, mk_order_ids=order_ids)
        elif self.bol_operations == 'import_shipments':
            action = self.env['stock.picking'].import_bol_shipment_for_open_order(instance)
        elif self.bol_operations == 'import_return':
            action = self.env['bol.return'].bol_import_returns(instance, from_date=self.bol_return_import_from_date)
        elif self.bol_operations == 'import_shipped_order':
            action = self.env['stock.picking'].bol_import_old_orders(instance)
        elif self.bol_operations == 'import_invoice_request' and instance.bol_export_invoice:
            action = self.env['bol.invoice.request'].get_open_import_request_for_invoice(instance)
        elif self.bol_operations == 'export_price':
            action = self.env['mk.listing'].update_price_in_bol_ts(instance)
        elif self.bol_operations == 'export_invoice' and instance.bol_export_invoice:
            action = self.env['bol.invoice.request'].export_pending_invoice_requests(instance)
        if action and type(action) == dict:
            return action
        return action

    def bol_get_active_cron_operation_wise(self):
        ir_cron_sudo = self.env['ir.cron'].sudo().with_context(active_test=False)
        mk_instance_id = self.mk_instance_id
        return {
            'bol': {
                'FBR': {
                    'import_order': ir_cron_sudo.search([('code', '=', f"model.cron_auto_import_bol_orders({mk_instance_id.id})")]),
                    'update_order': ir_cron_sudo.search([('code', '=', f"model.cron_auto_update_bol_order_status({mk_instance_id.id})")]),
                    'export_stock': ir_cron_sudo.search([('code', '=', f"model.cron_auto_export_bol_stock({mk_instance_id.id})")])
                },
                'FBB': {
                    'import_order': ir_cron_sudo.search([('code', '=', f"model.cron_auto_import_bol_orders({mk_instance_id.id})")]),
                    'import_stock': ir_cron_sudo.search([('code', '=', f"model.cron_auto_import_fbb_bol_stock({mk_instance_id.id})")])
                },
                'Both': {
                    'import_shipments': ir_cron_sudo.search([('code', 'in', [f"model.cron_auto_import_fbr_shipments({mk_instance_id.id})", f"model.cron_auto_import_fbb_shipments({mk_instance_id.id})"])]),
                    'import_return': ir_cron_sudo.search([('code', '=', f"model.cron_import_returns({mk_instance_id.id})")]),
                    'import_invoice_request': ir_cron_sudo.search([('code', '=', f"model.cron_auto_import_invoice_request({mk_instance_id.id})")]),
                    'export_price': ir_cron_sudo.search([('code', '=', f"model.cron_auto_export_bol_price({mk_instance_id.id})")]),
                    'export_invoice': ir_cron_sudo.search([('code', '=', f"model.cron_auto_export_bol_invoice({mk_instance_id.id})")])
                }
            }
        }
