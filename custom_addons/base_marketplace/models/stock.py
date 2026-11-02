from datetime import timedelta

from odoo import fields, models, _


class StockMove(models.Model):
    _inherit = "stock.move"

    def _get_new_picking_values(self):
        res = super(StockMove, self)._get_new_picking_values()
        order_id = self.sale_line_id.order_id
        if order_id.mk_id:
            res.update({'mk_instance_id': order_id.mk_instance_id.id})
            if order_id.updated_in_marketplace:
                res.update({'updated_in_marketplace': True})
        return res

    def _action_assign(self, force_qty=False):
        # Set the instance in drop-ship delivery orders.
        res = super(StockMove, self)._action_assign(force_qty=force_qty)
        picking_ids = self.filtered(lambda x: x.picking_id.sale_id and x.picking_id.sale_id.mk_instance_id and not x.picking_id.mk_instance_id).mapped('picking_id')
        for picking_id in picking_ids:
            picking_id.write({'mk_instance_id': picking_id.sale_id.mk_instance_id.id})
        return res

    def _assign_picking_post_process(self, new=False):
        """
        Task: T4859 - The post-processing of picking assignment to handle marketplace-specific scheduling.
        Args:
            new: indicating if a new picking was created.
        Returns:
            Res: True
        """
        res = super(StockMove, self)._assign_picking_post_process(new=new)
        order_id = self.sale_line_id.order_id
        create_date_raw = self.env.context.get('create_date', False)
        mk_instance = order_id.mk_instance_id
        use_security_lead = self.env['ir.config_parameter'].sudo().get_param('sale_stock.use_security_lead')
        vals = {}
        if mk_instance and new and create_date_raw and order_id.mk_id:
            create_date = fields.Datetime.to_datetime(create_date_raw)
            security_lead_time = order_id.company_id.security_lead
            picking_id = self.mapped('picking_id')
            if not security_lead_time or not use_security_lead:
                vals.update({'scheduled_date': create_date})
            if security_lead_time and use_security_lead:
                scheduled_date = create_date - timedelta(days=security_lead_time)
                vals.update({'scheduled_date': scheduled_date})
            if vals:
                picking_id.write(vals)
        return res


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    updated_in_marketplace = fields.Boolean("Updated in Marketplace?", default=False, copy=False)
    cancel_in_marketplace = fields.Boolean("Cancel in Marketplace", default=False, copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Marketplace Instance", ondelete='cascade', copy=False)
    marketplace = fields.Selection(related="mk_instance_id.marketplace", string='Marketplace')
    no_of_retry_count = fields.Integer(string="Retry Count", help="No of count that queue went in process.", compute_sudo=True)
    is_marketplace_exception = fields.Boolean(string="Marketplace Exception?", copy=False, help="Technical field to filter out problematic records from update order status process.")
    exception_message = fields.Text(string="Marketplace Exception Message", copy=False)
    is_fbm_order = fields.Boolean(string="Is Fulfillment By Marketplace Order?", copy=False)

    def update_order_status_to_marketplace(self):
        """
        Calling hook type method to update order status. Just need to add hook type method in individual marketplace app.
        :return: True
        """
        if hasattr(self, '%s_update_order_status_to_marketplace' % self.mk_instance_id.marketplace):
            getattr(self, '%s_update_order_status_to_marketplace' % self.mk_instance_id.marketplace)()
        return True

    def _action_done(self):
        res = super(StockPicking, self)._action_done()
        for picking_id in self:
            if picking_id.sale_id.invoice_status == 'invoiced':
                continue
            marketplace_workflow_id = picking_id.sale_id.order_workflow_id.sudo()
            delivery_lines = picking_id.move_line_ids.filtered(lambda l: l.product_id.invoice_policy == 'delivery')
            if delivery_lines and marketplace_workflow_id and marketplace_workflow_id.is_create_invoice and picking_id.picking_type_id.code == 'outgoing':
                picking_id.sale_id.with_context(manual_validate=True).process_invoice(marketplace_workflow_id)
        return res

    def do_marked_as_updated_in_odoo(self):
        self.ensure_one()
        self.updated_in_marketplace = True
        self.is_marketplace_exception = False


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def create_or_update_inventory_quant(self, location_id, product_id, quantity, name="", auto_validate=False):
        self = self.sudo()
        inventory_quant = self.search([('location_id', '=', location_id), ('product_id', '=', product_id.id)])
        if inventory_quant:
            inventory_quant.write({'inventory_quantity': quantity})
        else:
            inventory_quant = self.with_context(inventory_mode=True).create({'product_id': product_id.id, 'location_id': location_id, 'inventory_quantity': quantity})
        if auto_validate and product_id.tracking not in ['lot', 'serial']:
            inventory_quant.with_context(inventory_name=name).action_apply_inventory()
        return inventory_quant
