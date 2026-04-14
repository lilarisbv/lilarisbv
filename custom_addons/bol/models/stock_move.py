from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _get_new_picking_values(self):
        res = super(StockMove, self)._get_new_picking_values()
        order_id = self.sale_line_id.order_id
        if order_id.mk_id and order_id.marketplace == 'bol':
            res.update({'bol_fulfilment_method': order_id.bol_fulfilment_method, 'is_fbm_order': True if order_id.bol_fulfilment_method == 'FBB' else False})
        return res

    def _action_assign(self, force_qty=False):
        # Set the bol_fulfilment_method and is_fbm_order in drop-ship delivery orders.
        res = super(StockMove, self)._action_assign(force_qty=force_qty)
        picking_ids = self.filtered(lambda x: x.picking_id.sale_id and x.picking_id.sale_id.mk_instance_id and x.picking_id.sale_id.marketplace == 'bol' and not x.picking_id.bol_fulfilment_method).mapped('picking_id')
        for picking_id in picking_ids:
            picking_id.write({'bol_fulfilment_method': picking_id.sale_id.bol_fulfilment_method, 'is_fbm_order': True if picking_id.sale_id.bol_fulfilment_method == 'FBB' else False})
        return res
