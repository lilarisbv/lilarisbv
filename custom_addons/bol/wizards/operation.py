from datetime import timedelta
from odoo import models, fields, api, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.addons.bol.models.marketplace_listing import BOL_FULFILMENT_METHOD

FBR_OPERATIONS = [('import_order', 'Import Orders'), ('update_order', 'Update Tracking Details'), ('export_stock', 'Export Stock From Odoo to Bol')]
FBB_OPERATIONS = [('import_order', 'Import Orders'), ('import_stock', 'Import FBB Stock')]
BOL_OPERATIONS = [('import_product', 'Sync Offer/Products'), ('import_order_by_ids', 'Import Order By ids'), ('import_shipments', 'Import Shipments'),
                  ('import_return', 'Import Return'), ('import_shipped_order', 'Import Shipped Order'), ('export_price', 'Export Prices to Bol'),
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
            raise True
        if self.marketplace == 'bol':
            if self.bol_publish_in_store and not self.is_update_product:
                self.is_update_product = True

    def mk_add_to_listing(self):
        return super(MarketplaceOperation, self.with_context(bol_fulfilment_method=self.bol_fulfilment_method)).mk_add_to_listing()

    @api.onchange("bol_operation_type", "bol_fbr_operations", "bol_fbb_operations", "bol_operations")
    def onchange_bol_operations(self):
        if not self.mk_instance_id:
            raise True
        if self.marketplace == 'bol':
            selected_operation = False
            if self.bol_operation_type == 'FBR':
                selected_operation = self.bol_fbr_operations
            elif self.bol_operation_type == 'FBB':
                selected_operation = self.bol_fbb_operations
            else:
                selected_operation = self.bol_operations
            self.do_check_cron_status(self.bol_operation_type, selected_operation)

    def do_bol_operations(self):
        if not self.mk_instance_id:
            raise MarketplaceException(_("Please select marketplace instance to process."))
        instance = self.mk_instance_id
        res = True
        if self.bol_operation_type == 'FBR':
            if self.bol_fbr_operations == 'import_order':
                return self.env['sale.order'].bol_import_orders(instance)
            elif self.bol_fbr_operations == 'update_order':
                return self.env['sale.order'].bol_update_order_status(instance)
            elif self.bol_fbr_operations == 'export_stock':
                return self.env['mk.listing'].update_stock_in_bol_ts(instance)
        elif self.bol_operation_type == 'FBB':
            if self.bol_fbb_operations == 'import_order':
                if not instance.bol_fbb_warehouse_id:
                    raise MarketplaceException(_("Please define FBB warehouse in selected Bol instance >> Configuration >> FBB Warehouse and try again!"))
                if not instance.bol_fbb_workflow_id:
                    raise MarketplaceException(_("Please define FBB order workflow in selected Bol instance >> Workflow >> FBB Workflow and try again!"))
                return self.env['sale.order'].bol_import_orders(instance, type="FBB")
            elif self.bol_fbb_operations == 'import_stock':
                return self.env['mk.listing'].bol_import_fbb_stock(instance, auto_validate=self.bol_validate_inventory_adjustment)
        elif self.bol_operation_type == 'Both':
            if self.bol_operations == 'import_product':
                return self.env['mk.listing'].bol_import_listings(instance, mk_listing_id=self.mk_listing_id, update_product_price=self.update_product_price, update_existing_product=self.update_existing_product)
            elif self.bol_operations == 'import_order_by_ids':
                order_ids = [order_id.strip() for order_id in self.mk_order_id.split(',')]
                return self.env['sale.order'].bol_import_order_by_ids(instance, mk_order_ids=order_ids)
            elif self.bol_operations == 'import_shipments':
                return self.env['stock.picking'].import_bol_shipment_for_open_order(instance)
            elif self.bol_operations == 'import_return':
                return self.env['bol.return'].bol_import_returns(instance, from_date=self.bol_return_import_from_date)
            elif self.bol_operations == 'import_shipped_order':
                return self.env['stock.picking'].bol_import_old_orders(instance)
            elif self.bol_operations == 'export_price':
                return self.env['mk.listing'].update_price_in_bol_ts(instance)

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
                    'export_price': ir_cron_sudo.search([('code', '=', f"model.cron_auto_export_bol_price({mk_instance_id.id})")])
                }
            }
        }
