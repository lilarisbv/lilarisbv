from odoo import fields, models, _


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def woocommerce_update_order_status_to_marketplace(self):
        self.env['sale.order'].process_update_order_status_woocommerce(self.sale_id, self.mk_instance_id)
        return True

    def _send_confirmation_email(self):
        newself = self
        for stock_pick in self.filtered(lambda p: p.company_id.stock_move_email_validation and p.picking_type_id.code == 'outgoing'):
            if (stock_pick.mk_instance_id and stock_pick.mk_instance_id.marketplace == 'woocommerce') or stock_pick.sale_id.marketplace == 'woocommerce':
                newself -= stock_pick
        return super(StockPicking, newself)._send_confirmation_email()
