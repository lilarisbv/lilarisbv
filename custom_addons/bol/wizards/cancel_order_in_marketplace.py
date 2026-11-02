import datetime
import time

from odoo import models, api, fields, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from ..models.bol_process_status import BOL_PROCESS_STATUS_MAX_CHECKS

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
        waiting_items = []
        for item_line in self.bol_cancel_item_line_ids:
            order_line = item_line.order_line_id
            # A cancel that was sent a moment ago and is not answered yet must not be sent twice.
            sent = order_line.bol_cancel_process_id
            if sent.state == 'pending' and sent.create_date > fields.Datetime.now() - datetime.timedelta(days=1):
                waiting_items.append(order_line.name)
                continue
            response = mk_instance_id._send_bol_request('retailer/orders/cancellation', {'orderItems': [{'orderItemId': order_line.mk_id, 'reasonCode': item_line.bol_cancel_reason}]}, method="PUT")
            process_id = self.env['bol.process.status'].create_or_update_process_status(response, mk_instance_id)
            order_line.bol_cancel_process_id = process_id
            for attempt in range(1, BOL_PROCESS_STATUS_MAX_CHECKS + 1):
                process_id.get_process_status()
                if process_id.state == 'success':
                    order_line._apply_bol_cancel_result(process_id)
                    break
                if process_id.state in ['failure', 'timeout']:
                    raise MarketplaceException(_("Failed to Cancel Order '{}'. \nSTATUS: {} \nERROR: {}".format(order_id.name, process_id.state.upper(), process_id.error_message)))
                if attempt < BOL_PROCESS_STATUS_MAX_CHECKS:
                    time.sleep(min(2 ** attempt, 30))
            else:
                # Still pending: do not raise (that would roll back the process record). The webhook or the scheduled action finish it later.
                waiting_items.append(order_line.name)
            self.env.cr.commit()
        if waiting_items:
            message = _("Not finished at Bol.com yet: %s. Odoo cancels the item(s) when Bol.com answers.") % ', '.join(waiting_items)
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'message': message, 'type': 'warning', 'sticky': False, 'next': {'type': 'ir.actions.act_window_close'}}}
        return True


class BolCancelItemLine(models.TransientModel):
    _name = "bol.cancel.item.line"
    _description = "Bol Order Items"

    order_line_id = fields.Many2one('sale.order.line', string="Order Line", ondelete='cascade')
    bol_cancel_reason = fields.Selection(BOL_CANCEL_REASON_CODES, "Cancel Reason")
    wizard_id = fields.Many2one('mk.cancel.order', 'Wizard')
