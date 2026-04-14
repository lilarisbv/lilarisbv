from odoo import models, api, fields, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException

BOL_CANCEL_REASON_CODES = [('OUT_OF_STOCK', 'OUT OF STOCK / ALREADY SOLD THROUGH ANOTHER CHANNEL / WEBSITE'),
                           ('REQUESTED_BY_CUSTOMER', 'REQUESTED BY CUSTOMER'),
                           ('NOT_AVAIL_IN_TIME', 'NOT AVAILABLE IN TIME'),
                           ('BAD_CONDITION', 'THE ITEM WAS NO LONGER IN GOOD CONDITION'),
                           ('RETAIN_ITEM', 'I WANTED TO KEEP THE ITEM ANYWAY'),
                           ('UNFINDABLE_ITEM', "I COULDN'T FIND THE ARTICLE ANYMORE"),
                           ('INCORRECT_PRICE', "THE PRICING WAS NOT GOOD"),
                           ('HIGHER_SHIPCOST', "SHIPPING COSTS WERE HIGHER THAN EXPECTED"),
                           ('TECH_ISSUE', "TECHNICAL ISSUE"),
                           ('NO_BOL_GUARANTEE', "ARTICLE IS NOT COVERED BY BOL.COM WARRANTY"),
                           ('ORDERED_TWICE', "DUPLICATE ORDER"),
                           ('OTHER', "OTHERWISE")]


class MKCancelOrder(models.TransientModel):
    _inherit = "mk.cancel.order"

    bol_cancel_item_line_ids = fields.One2many('bol.cancel.item.line', 'wizard_id', string="Order Items")

    def do_cancel_in_bol(self):
        active_id = self.env.context.get('active_id')
        order_id = self.env['sale.order'].browse(active_id)
        if not order_id:
            raise MarketplaceException(_("Can't find order to cancel. Please go back to order list, open order and try again!"))
        if any([not item.bol_cancel_reason for item in self.bol_cancel_item_line_ids]):
            raise MarketplaceException(_("'Cancel Reason' is mandatory for each line."))
        mk_instance_id = order_id.mk_instance_id
        cancelled_bol_item_ids = []
        for item_line in self.bol_cancel_item_line_ids:
            response = mk_instance_id._send_bol_request('retailer/orders/cancellation', {'orderItems': [{'orderItemId': item_line.order_line_id.mk_id, 'reasonCode': item_line.bol_cancel_reason}]}, method="PUT")
            process_id = self.env['bol.process.status'].create_or_update_process_status(response, mk_instance_id)
            while True:
                process_id.get_process_status()
                if process_id.state == 'success' and process_id.entity_id:
                    cancelled_bol_item_ids.append(item_line.order_line_id.mk_id)
                    order_id.message_post(body=_("Order item '{}' has been successfully cancelled in bol.com".format(item_line.order_line_id.name)))
                    break
                if process_id.state in ['failure', 'timeout']:
                    raise MarketplaceException(_("Failed to Cancel Order '{}'. \nSTATUS: {} \nERROR: {}".format(order_id.name, process_id.state.upper(), process_id.error_message)))
            self.env.cr.commit()
        if cancelled_bol_item_ids:
            if all([order_line.mk_id in cancelled_bol_item_ids for order_line in order_id.order_line]):
                order_id.write({'canceled_in_marketplace': True})
                order_id.message_post(body=_("Order '{}' has been successfully cancelled in bol.com".format(order_id.name)))
            else:
                order_id.action_draft()
                order_id.order_line.filtered(lambda x: x.mk_id in cancelled_bol_item_ids).write({'product_uom_qty': 0.0})
                order_id.action_confirm()
        return True

class BolCancelItemLine(models.TransientModel):
    _name = "bol.cancel.item.line"
    _description = "Bol Order Items"

    order_line_id = fields.Many2one('sale.order.line', string="Order Line", ondelete='cascade')
    bol_cancel_reason = fields.Selection(BOL_CANCEL_REASON_CODES, "Cancel Reason")
    wizard_id = fields.Many2one('mk.cancel.order', 'Wizard')
