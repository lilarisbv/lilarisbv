import datetime
import logging
import pprint
import time

from psycopg2 import OperationalError

from odoo import fields, models, tools, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.tools import float_is_zero
from .bol_process_status import BOL_PROCESS_STATUS_MAX_CHECKS
from .misc import log_traceback_for_exception

_logger = logging.getLogger("TeqStars:bol")

BOL_FULFILMENT_METHOD = [('FBB', 'Fulfilment by bol.com'), ('FBR', 'Fulfilment by retailer')]

# A shipment that cannot be exported is retried this many times by the scheduled action before it is
# marked as a marketplace exception and left for manual processing.
BOL_SHIPMENT_MAX_RETRY = 3


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    bol_fulfilment_method = fields.Selection(BOL_FULFILMENT_METHOD, string='Fulfilment Method (Bol)', help='Specifies whether this shipment has been fulfilled by the retailer (FBR) or fulfilled by bol.com (FBB). Defaults to FBR.')
    bol_shipment_numbers = fields.Char("Bol Shipment(s)", help="Shipment identification")
    bol_shipment_process_ids = fields.Many2many('bol.process.status', 'bol_picking_shipment_process_rel', 'picking_id', 'process_id', string="Pending Shipment Process", copy=False, readonly=True,
                                                help="Bol.com process status of the shipment request(s) that were not answered yet. Odoo finishes the export when Bol.com answers, even hours later.")

    def open_website_url(self):
        self.ensure_one()
        if self.mk_instance_id and self.mk_instance_id.marketplace == 'bol':
            tracking_id = self.carrier_id.bol_transporter_id.tracking_ids.filtered(lambda x: self.partner_id.country_id in x.country_ids)
            if tracking_id:
                tracking_url = tracking_id.tracking_url
                if '{trackTraceCode}' in tracking_url:
                    tracking_url = tracking_url.replace("{trackTraceCode}", self.carrier_tracking_ref)
                if '{zipCode}' in tracking_url:
                    tracking_url = tracking_url.replace('{zipCode}', self.partner_id.zip)
                return {
                    'type': 'ir.actions.act_url',
                    'name': "Shipment Tracking Page",
                    'target': 'new',
                    'url': tracking_url,
                }
        return super(StockPicking, self).open_website_url()

    def _send_confirmation_email(self):
        newself = self
        for stock_pick in self.filtered(lambda p: p.company_id.stock_move_email_validation and p.picking_type_id.code == 'outgoing'):
            if (stock_pick.mk_instance_id and stock_pick.mk_instance_id.marketplace == 'bol') or stock_pick.sale_id.marketplace == 'bol':
                newself -= stock_pick
        return super(StockPicking, newself)._send_confirmation_email()

    def _action_done(self):
        for picking in self.filtered(lambda x: x.picking_type_id.code == 'outgoing' and ((x.mk_instance_id and x.mk_instance_id.marketplace == 'bol') or x.sale_id.marketplace == 'bol')):
            if not picking.sale_id:
                continue
            mk_instance_id, order = picking.sale_id.mk_instance_id, picking.sale_id
            order_response = mk_instance_id.with_context(skip_raise=True)._send_bol_request('retailer/orders/{}'.format(order.mk_id), {})
            if not order_response is True:
                already_cancelled_items_on_bol = [item.get('orderItemId') for item in order_response.get('orderItems') if item['cancellationRequest'] or item['quantity'] == item['quantityCancelled']]
                remaining_to_ship = []
                error_msg = ''
                for move in picking.move_ids:
                    mk_id = move.sale_line_id.mk_id
                    order_line = move.sale_line_id
                    if not mk_id or not move.quantity:
                        continue
                    if mk_id in already_cancelled_items_on_bol:
                        # if order_line.product_uom_qty:
                        #     with self.pool.cursor() as custom_cr:
                        #         order_line.order_id.with_env(self.env(cr=custom_cr)).message_post(body=_("Order item '{}' already cancelled in bol.com. Updating ordered quantity to 0 (Zero).".format(move.sale_line_id.name)))
                        #         order_line.with_env(self.env(cr=custom_cr)).with_context(skip_procurement=True).write({'product_uom_qty': 0.0})
                        error_msg += "\nThe item '{}' has already cancelled on Bol.com".format(move.sale_line_id.name)
                    else:
                        remaining_to_ship.append(mk_id)
                if error_msg:
                    if remaining_to_ship:
                        error_msg += "\n\nTo handle this, you can set the 'Done' quantity to 0 for the canceled item and specify the respective quantity for the item you are shipping. Afterward, you can validate the order again, ensuring to select the 'NO BACKORDER' option."
                    raise MarketplaceException(_(error_msg))
        return super()._action_done()

    def _raise_or_log_shipment_export_error(self, message, manual_process, prefix="UPDATE ORDER STATUS"):
        """Raise the error for a manual run, or log it and count a failed automatic attempt.

        Follows bol.invoice.request._raise_or_log_invoice_export_error: a manual run always raises so
        the user sees the reason, while the cron records the failure and counts it. Once the attempts
        are used up the picking is marked as a marketplace exception, which takes it out of the
        scheduled action for good. The manual button ignores all of this and can always be used.
        """
        mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        msg = f"{prefix}: {message}"
        if manual_process:
            raise MarketplaceException(msg)
        if self.id and not manual_process:
            retry_count = (self.no_of_retry_count or 0) + 1
            picking_vals = {'no_of_retry_count': retry_count}
            if retry_count >= BOL_SHIPMENT_MAX_RETRY:
                picking_vals.update({'is_marketplace_exception': True, 'exception_message': msg})
            self.write(picking_vals)
        mk_log_line_dict['error'].append({'log_message': msg})
        return False

    def _get_bol_distribution_item_ids(self, order_id):
        """Return the order item ids whose transport bol.com arranges (fulfilment.distributionParty = BOL).

        Their label is bought through bol.com (e.g. by Sendcloud) and is bound to all the items on it.
        Any failure returns an empty set, so the items keep the regular one-request-per-item export.
        """
        try:
            order_response = self.mk_instance_id.with_context(skip_raise=True)._send_bol_request('retailer/orders/{}'.format(order_id.mk_id), {})
        except Exception as e:
            _logger.warning("Bol order %s: could not read distribution party, exporting items one by one. %s", order_id.name, e)
            return set()
        if not isinstance(order_response, dict):
            return set()
        return {item.get('orderItemId') for item in order_response.get('orderItems', [])
                if (item.get('fulfilment') or {}).get('distributionParty') == 'BOL'}

    @staticmethod
    def _bol_prepare_shipment_requests(zipped_items, bol_distribution_item_ids):
        """Turn (order_item_id, tracking_number) pairs into [([order_item_id, ...], tracking_number), ...].

        Items whose transport the retailer arranges keep one request per item. Items whose transport
        bol.com arranges are sent together per tracking number, because bol.com rejects a shipment
        that lists only part of the items on its label ("Request shipment is incomplete ...").
        """
        requests, bol_requests = [], {}
        for item_id, tracking_number in zipped_items:
            if item_id in bol_distribution_item_ids:
                if tracking_number not in bol_requests:
                    bol_requests[tracking_number] = []
                    requests.append((bol_requests[tracking_number], tracking_number))
                bol_requests[tracking_number].append(item_id)
            else:
                requests.append(([item_id], tracking_number))
        return requests

    def do_bol_update_order_status(self, manual_process=False):
        mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        waiting_pickings = []
        for picking in self:
            # Skip automatic retries once the attempts are used up. A manual run is never skipped.
            if not manual_process and (picking.no_of_retry_count or 0) >= BOL_SHIPMENT_MAX_RETRY:
                continue
            # A shipment that was sent a moment ago and is not answered yet must not be sent twice.
            if picking._bol_has_pending_shipment_process():
                waiting_pickings.append(picking.name)
                continue
            order_id = picking.sale_id
            mk_instance_id = self.mk_instance_id
            bol_transporter_code = picking.carrier_id.bol_transporter_id.code
            if not bol_transporter_code:
                if picking.carrier_id:
                    transporter_message = _("Cannot update order status of Order %s because Bol Transporter is not set in shipping method %s.") % (order_id.name, picking.carrier_id.name)
                else:
                    transporter_message = _("Cannot update order status of Order %s because shipping method is not set in Delivery %s.") % (order_id.name, picking.name)
                picking._raise_or_log_shipment_export_error(transporter_message, manual_process)
                continue
            if any([line.product_id.type != 'service' and not line.mk_id for line in order_id.order_line]):
                picking._raise_or_log_shipment_export_error(_("Cannot update order status because Bol Order Line ID not found in Order %s.") % order_id.name, manual_process)
                continue
            bol_shipments = mk_instance_id._send_bol_request('retailer/shipments?order-id={}'.format(order_id.mk_id), {})
            # Ordered, not a set: item n is matched with tracking number n. All items of the picking count for
            # the matching, also those already on a Bol shipment, so a retry keeps each item's tracking number.
            picking_items, order_items_ids = {}, []
            for move in picking.move_ids:
                bol_order_item_id = move.sale_line_id.mk_id or False
                if bol_order_item_id and bol_order_item_id not in picking_items:
                    picking_items[bol_order_item_id] = move.sale_line_id
                    already_updated_in_bol = False
                    for shipment in bol_shipments.get('shipments', []):
                        for shipment_item in shipment.get("shipmentItems"):
                            if shipment_item.get("orderItemId") == bol_order_item_id:
                                already_updated_in_bol = True
                                break
                    if not already_updated_in_bol:
                        order_items_ids.append(bol_order_item_id)
            export_failed = False
            pending_process_ids = self.env['bol.process.status']
            log_prefix = "UPDATE ORDER STATUS: Bol order %s, picking %s:" % (order_id.name, picking.name)
            if order_items_ids:
                try:
                    # Not de-duplicated: a repeated tracking number keeps its position (two items in one parcel),
                    # removing it would shift every following item onto the next item's tracking number.
                    tracking_list = [tracking.strip() for tracking in (picking.carrier_tracking_ref or '').split(',') if tracking.strip()] or ['']
                    # Item n gets tracking number n, items past the last tracking number reuse it (several items in
                    # one parcel). Tracking numbers past the last item have no item to go with: logged, never sent.
                    zipped_items = [(item_id, tracking_list[min(index, len(tracking_list) - 1)]) for index, item_id in enumerate(picking_items)]
                    used_trackings = set(tracking_list[:len(picking_items)])
                    unused_trackings = list(dict.fromkeys(tracking for tracking in tracking_list[len(picking_items):] if tracking not in used_trackings))
                    _logger.info("%s %s order line(s), %s still to ship, %s tracking number(s).", log_prefix, len(picking_items), len(order_items_ids), len([tracking for tracking in tracking_list if tracking]))
                    zipped_items = [(item_id, tracking_number) for item_id, tracking_number in zipped_items if item_id in order_items_ids]
                    for tracking_number in unused_trackings:
                        _logger.warning("%s tracking number %s not sent to Bol.com, there is no order item left to match it with.", log_prefix, tracking_number)
                    if unused_trackings:
                        picking.message_post(body=_("Tracking number(s) %s not sent to Bol.com: this delivery has more tracking numbers than order lines.") % ', '.join(unused_trackings))
                    bol_distribution_item_ids = picking._get_bol_distribution_item_ids(order_id) if len(order_items_ids) > 1 else set()
                    for item_ids, tracking_number in self._bol_prepare_shipment_requests(zipped_items, bol_distribution_item_ids):
                        request_dict = {'orderItems': [{'orderItemId': item_id} for item_id in item_ids],
                                        'shipmentReference': picking.name,
                                        'transport': {'transporterCode': bol_transporter_code,
                                                      'trackAndTrace': tracking_number}}
                        _logger.info("%s POST shipment, order items %s, transporter %s, tracking number %s.", log_prefix, item_ids, bol_transporter_code, tracking_number or '-')
                        response = mk_instance_id._send_bol_request('retailer/shipments', request_dict, method="POST")
                        _logger.info("%s Bol.com answered process status %s (%s).", log_prefix, response.get('processStatusId'), response.get('status'))
                        process_id = self.env['bol.process.status'].create_or_update_process_status(response, mk_instance_id)
                        for attempt in range(1, BOL_PROCESS_STATUS_MAX_CHECKS + 1):
                            process_id.get_process_status()
                            if process_id.state == 'success':
                                break
                            if process_id.state in ['failure', 'timeout']:
                                log_message = _("Order %s is not updated in Bol.com. STATUS: %s REASON: %s") % (order_id.name, process_id.state.upper(), process_id.error_message)
                                _logger.warning("%s shipment of order items %s refused: %s", log_prefix, item_ids, log_message)
                                if manual_process:
                                    raise MarketplaceException(log_message)
                                else:
                                    export_failed = True
                                    picking._raise_or_log_shipment_export_error(log_message, manual_process)
                                    break
                            if attempt < BOL_PROCESS_STATUS_MAX_CHECKS:
                                time.sleep(min(2 ** attempt, 30))
                        else:
                            # Still pending after the quick checks: not a failed attempt (that would count towards the retries and could never be undone
                            # when Bol.com answers later). The webhook, 'Get Status' or the scheduled action finish it, and a run of this export
                            # reads the shipments of Bol.com first, so an item that Bol.com did ship is never sent twice.
                            _logger.info("%s shipment of order items %s is not finished at Bol.com yet (process status %s).", log_prefix, item_ids, process_id.mk_id)
                            picking.bol_shipment_process_ids |= process_id
                            pending_process_ids |= process_id
                        time.sleep(8)
                except Exception as e:
                    _logger.error("%s shipment export failed: %s", log_prefix, e)
                    if manual_process:
                        raise MarketplaceException(e, additional_context={'show_traceback': True})
                    else:
                        export_failed = True
                        picking._raise_or_log_shipment_export_error(_("Bol Order %s, ERROR: %s.") % (order_id.name, e), manual_process)
            bol_shipments = mk_instance_id._send_bol_request('retailer/shipments?order-id={}'.format(order_id.mk_id), {})
            # Only close the delivery when Bol.com actually reports shipments and nothing failed above,
            # otherwise the write below would reset the attempt counter that was just increased.
            if pending_process_ids:
                # Waiting for Bol.com: the delivery stays open and the attempt counter is left alone.
                waiting_pickings.append(picking.name)
            elif not export_failed and bol_shipments and bol_shipments.get('shipments'):
                shipment_numbers = picking._bol_mark_exported(bol_shipments)
                _logger.info("%s done, Bol shipment(s) %s.", log_prefix, shipment_numbers)
                not manual_process and mk_log_line_dict['success'].append({'log_message': _('UPDATE ORDER STATUS: Successfully updated Bol order %s. Bol Shipment(s): %s') % (order_id.name, shipment_numbers)})
            else:
                _logger.warning("%s not closed, export failed: %s, retry count: %s.", log_prefix, export_failed, picking.no_of_retry_count)
            self.env.cr.commit()
        if manual_process and waiting_pickings:
            message = _("Not finished at Bol.com yet: %s. Odoo closes the delivery when Bol.com answers.") % ', '.join(waiting_pickings)
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'message': message, 'type': 'warning', 'sticky': False}}
        return True

    def bol_update_order_status_to_marketplace(self):
        return self.filtered(lambda x: x.mk_instance_id.marketplace == 'bol').do_bol_update_order_status(manual_process=True)

    def update_order_status_to_marketplace(self):
        # The base method drops what the marketplace hook returns, so the "not finished at Bol.com yet" notice would never reach the button.
        if len(self) == 1 and self.mk_instance_id.marketplace == 'bol':
            return self.bol_update_order_status_to_marketplace()
        return super().update_order_status_to_marketplace()

    def _bol_has_pending_shipment_process(self):
        self.ensure_one()
        one_day_ago = fields.Datetime.now() - datetime.timedelta(days=1)
        return bool(self.bol_shipment_process_ids.filtered(lambda process: process.state == 'pending' and process.create_date > one_day_ago))

    def _bol_mark_exported(self, bol_shipments):
        """Close the delivery: Bol.com holds its shipment(s). Returns the Bol shipment numbers."""
        self.ensure_one()
        shipment_numbers = ','.join([str(shipment.get('shipmentId')) for shipment in bol_shipments.get('shipments')])
        self.write({'updated_in_marketplace': True, 'is_marketplace_exception': False, 'exception_message': False, 'no_of_retry_count': 0, 'bol_shipment_numbers': shipment_numbers})
        return shipment_numbers

    def _bol_close_if_exported(self):
        """Close the delivery when Bol.com holds a shipment for every order item of it (read from Bol.com, nothing is sent)."""
        self.ensure_one()
        if self.updated_in_marketplace:
            return True
        order_id = self.sale_id
        bol_shipments = self.mk_instance_id._send_bol_request('retailer/shipments?order-id={}'.format(order_id.mk_id), {})
        if not isinstance(bol_shipments, dict) or not bol_shipments.get('shipments'):
            return False
        shipped_items = {item.get('orderItemId') for shipment in bol_shipments['shipments'] for item in shipment.get('shipmentItems') or []}
        picking_items = {move.sale_line_id.mk_id for move in self.move_ids if move.sale_line_id.mk_id}
        if not picking_items or not picking_items <= shipped_items:
            return False
        self._bol_mark_exported(bol_shipments)
        return True

    def _apply_bol_shipment_result(self, process_id):
        """Called once by bol.process.status when a shipment request of the delivery that was still pending leaves pending.
        SUCCESS closes the delivery when Bol.com holds all its items, failure or timeout counts as one failed attempt, like the export does."""
        for picking in self:
            if process_id.state == 'success':
                picking._bol_close_if_exported()
            elif process_id.state in ['failure', 'timeout']:
                message = _("Order %s is not updated in Bol.com. STATUS: %s REASON: %s") % (picking.sale_id.name, (process_id.state or '').upper(), process_id.error_message or '')
                picking.message_post(body=message)
                picking._raise_or_log_shipment_export_error(message, manual_process=False)

    def fetch_shipment_from_bol(self, mk_instance_id, type="FBR", import_old_shipments=False):
        bol_shipment_list, page = [], 1 if import_old_shipments else ((mk_instance_id.bol_fbb_last_shipment_page_number if type == 'FBB' else mk_instance_id.bol_fbr_last_shipment_page_number) or 1)
        while True:
            response = mk_instance_id._send_bol_request('retailer/shipments?fulfilment-method={}&page={}'.format(type, page), {})
            if not response.get('shipments'):
                page -= 1
                break
            bol_shipment_list += response.get('shipments')
            if len(response.get('shipments')) < 50:
                break
            page += 1
        mk_instance_id.write({'bol_fbb_last_shipment_page_number': page}) if type == 'FBB' else mk_instance_id.write({'bol_fbr_last_shipment_page_number': page})
        return bol_shipment_list

    def bol_import_old_orders(self, mk_instance_id, fetch_all_orders=True):
        res_id_list = []
        valid_fulfillment_options = mk_instance_id.bol_get_valid_fulfillment_options()
        for fulfillment_option in valid_fulfillment_options:
            bol_shipment_list = self.fetch_shipment_from_bol(mk_instance_id, fulfillment_option, import_old_shipments=fetch_all_orders)
            bol_order_list = list(set([bol_shipment.get('order', {}).get('orderId', '') for bol_shipment in bol_shipment_list]))
            if bol_order_list:
                batch_size = mk_instance_id.queue_batch_limit or 100
                for bol_order in tools.split_every(batch_size, bol_order_list):
                    queue_id = mk_instance_id.action_create_queue(type='order')
                    for bol_order_id in bol_order:
                        line_vals = {
                            'mk_id': bol_order_id,
                            'state': 'draft',
                            'name': bol_order_id.strip(),
                            'data_to_process': pprint.pformat(dict()),
                            'mk_instance_id': mk_instance_id.id,
                        }
                        queue_id.action_create_queue_lines(line_vals)
                    res_id_list.append(queue_id.id)
        if res_id_list:
            return mk_instance_id.action_open_model_view(res_id_list, 'mk.queue.job', 'Bol.com Old Order Queue')
        return True

    def get_open_order_for_bol(self, mk_instance_id, fulfillment_type=False):
        valid_fulfillment_options = [fulfillment_type] if fulfillment_type else [mk_instance_id.bol_operation_type]
        if mk_instance_id.bol_operation_type == 'Both' and not fulfillment_type:
            valid_fulfillment_options = ['FBR', 'FBB']
        # Commented Problem: if user recently changed warehouse on instance so that Old open order won't filter.
        # warehouse_ids = self.env['stock.warehouse']
        # if mk_instance_id.bol_operation_type in ['FBR', 'Both']:
        #     warehouse_ids += mk_instance_id.warehouse_id
        # if mk_instance_id.bol_operation_type in ['FBB', 'Both']:
        #     warehouse_ids += mk_instance_id.bol_fbb_warehouse_id
        open_delivery_orders = self.env['stock.picking'].search(
            ['|', ('mk_instance_id', '=', mk_instance_id.id), ('backorder_id.mk_instance_id', '=', mk_instance_id.id),
             ('updated_in_marketplace', '=', False), ('backorder_id', '=', False),
             ('state', 'in', ['confirmed', 'assigned']),
             ('picking_type_id.code', '=', 'outgoing'),
             # ('picking_type_id.warehouse_id', 'in', warehouse_ids.ids),
             ('bol_fulfilment_method', 'in', valid_fulfillment_options)], order='id asc')
        open_delivery_orders.filtered(lambda x: not x.mk_instance_id).write({'mk_instance_id': mk_instance_id.id})
        return open_delivery_orders.mapped('sale_id').filtered(lambda x: not x.bol_is_skipped_for_import_shipment)

    def _update_bol_tracking_detail(self, transporter_code, carrier_tracking_ref, mk_instance_id):
        carrier_id = self.env['delivery.carrier'].bol_search_create_delivery_carrier(transporter_code, mk_instance_id)
        self.write({'carrier_id': carrier_id.id, 'carrier_tracking_ref': carrier_tracking_ref})
        return True

    def _bol_apply_shipment_data_to_order(self, order, shipment_data_list, mk_instance_id, mrp_installed=None):
        """Apply already-fetched full Bol shipment payloads onto the order's open picking.

        Shared by the polling import (import_bol_shipment_for_open_order) and the
        SHIPMENT webhook (_process_single_bol_shipment). Returns (success_messages, picking_id).
        """
        if mrp_installed is None:
            mrp_installed = self.env['ir.module.module'].sudo().search([('name', '=', 'mrp'), ('state', '=', 'installed')])
        success_messages, picking_id = [], False
        carrier_wise_shipment_dict = {}
        for shipment_data in shipment_data_list:
            if not isinstance(shipment_data, dict):
                continue
            transporter_code = shipment_data.get('transport', {}).get('transporterCode', '')
            carrier_wise_shipment_dict.setdefault(transporter_code, []).append(shipment_data)
        precision_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        bol_shipment_numbers = set([])
        for code, shipments in carrier_wise_shipment_dict.items():
            picking_id = order.picking_ids.filtered(lambda x: x.state not in ['done', 'cancel'])
            carrier_tracking_set = set([])
            for shipment in shipments:
                for shipment_item in shipment.get('shipmentItems', []):
                    if shipment_item.get('fulfilment', {}).get('method') != order.bol_fulfilment_method:
                        continue
                    order_line = order.order_line.filtered(lambda x: x.mk_id == shipment_item.get('orderItemId'))
                    if order_line.qty_delivered == order_line.product_uom_qty:
                        continue
                    product_id = order_line.product_id
                    bom_lines = False
                    if mrp_installed:
                        bom = self.env['mrp.bom'].sudo()._bom_find(products=product_id, company_id=order.company_id.id, bom_type='phantom')[product_id]
                        if bom:
                            factor = product_id.uom_id._compute_quantity(float(shipment_item.get('quantity')), bom.uom_id) / bom.product_qty
                            boms, bom_lines = bom.sudo().explode(product_id, factor, picking_type=bom.picking_type_id)
                    if bom_lines:
                        for bom_line, line_data in bom_lines:
                            stock_move = picking_id.move_ids.filtered(lambda x: x.sale_line_id.id == order_line.id and x.product_id == bom_line.product_id)
                            stock_move._action_assign()
                            stock_move.move_line_ids.filtered(lambda ml: ml.state not in ('done', 'cancel')).quantity = 0
                            stock_move._set_quantity_done(line_data['qty'])
                    else:
                        stock_move = picking_id.move_ids.filtered(lambda x: x.sale_line_id.id == order_line.id)
                        stock_move._action_assign()
                        stock_move.move_line_ids.filtered(lambda ml: ml.state not in ('done', 'cancel')).quantity = 0
                        stock_move._set_quantity_done(float(shipment_item.get('quantity')))
                    bol_shipment_numbers.add(shipment.get('shipmentId'))
                    carrier_tracking_set.add(shipment.get('transport', {}).get('trackAndTrace', ''))
            if all(float_is_zero(move_line.quantity, precision_digits=precision_digits) for move_line in picking_id.move_line_ids.filtered(lambda m: m.state not in ('done', 'cancel'))):
                continue
            picking_id._update_bol_tracking_detail(code, ','.join(list(carrier_tracking_set)), mk_instance_id)
            res = picking_id.with_context(skip_sms=True).button_validate()
            if isinstance(res, dict):
                if res.get('res_model', False):
                    record = self.env[res.get('res_model')].sudo().with_context(res.get("context")).create({})
                    record.process()
            if picking_id.state == 'done':
                picking_id.write({'updated_in_marketplace': True, 'is_marketplace_exception': False, 'exception_message': False, 'bol_shipment_numbers': ','.join(list(bol_shipment_numbers))})
                success_messages.append(_('IMPORT SHIPMENT : Order# {} ({}) successfully shipped in Odoo.'.format(order.name, order.bol_fulfilment_method)))
        return success_messages, picking_id

    def _process_single_bol_shipment(self, shipment_id, mk_instance_id):
        """Process one shipment identified by a SHIPMENT webhook (resourceId = shipmentId)."""
        shipment_data = mk_instance_id._send_bol_request('retailer/shipments/{}'.format(shipment_id), {})
        if not isinstance(shipment_data, dict):
            return False
        order_mk_id = shipment_data.get('order', {}).get('orderId')
        if not order_mk_id:
            return False
        fulfilment_methods = list({item.get('fulfilment', {}).get('method') for item in shipment_data.get('shipmentItems', []) if item.get('fulfilment')})
        domain = [('mk_id', '=', order_mk_id), ('mk_instance_id', '=', mk_instance_id.id)]
        if fulfilment_methods:
            domain.append(('bol_fulfilment_method', 'in', fulfilment_methods))
        order = self.env['sale.order'].search(domain, limit=1)
        if not order:
            # Order not imported yet: enqueue an order import so it is created first; the
            # next shipment webhook or the polling cron will then complete the shipment.
            queue_id = mk_instance_id.action_create_queue(type='order')
            queue_id.action_create_queue_lines({'mk_id': order_mk_id, 'name': order_mk_id, 'state': 'draft',
                                                'data_to_process': pprint.pformat(dict()), 'mk_instance_id': mk_instance_id.id})
            return False
        open_picking = order.picking_ids.filtered(lambda x: x.state not in ['done', 'cancel'] and x.picking_type_id.code == 'outgoing')
        if not open_picking:
            return False
        success_messages, picking_id = self._bol_apply_shipment_data_to_order(order, [shipment_data], mk_instance_id)
        if success_messages:
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='webhook',
                                                 mk_log_line_dict={'success': [{'log_message': m} for m in success_messages]})
        return True

    def import_bol_shipment_for_open_order(self, mk_instance_id, fulfillment_type=False):
        mk_log_line_dict = {'error': [], 'success': []}
        order_ids = self.get_open_order_for_bol(mk_instance_id, fulfillment_type=fulfillment_type)
        mrp_installed = self.env['ir.module.module'].sudo().search([('name', '=', 'mrp'), ('state', '=', 'installed')])
        valid_fulfillment_options = list(set(order_ids.mapped('bol_fulfilment_method')))
        bol_shipment_dict = {fulfilment_method: {} for fulfilment_method in valid_fulfillment_options}
        for fulfilment_method in valid_fulfillment_options:
            bol_shipment_list = self.fetch_shipment_from_bol(mk_instance_id, fulfilment_method, import_old_shipments=True)
            for bol_shipment in bol_shipment_list:
                order_id = bol_shipment.get('order', {}).get('orderId', False)
                bol_shipment_dict[fulfilment_method].setdefault(order_id, []).append(bol_shipment)
        for orders in tools.split_every(5, order_ids, piece_maker=list):
            for order in orders:
                try:
                    picking_id = False
                    order_shipments = bol_shipment_dict[order.bol_fulfilment_method].get(order.mk_id, [])
                    if not order_shipments:
                        log_message = _('IMPORT SHIPMENT : Order# {} ({}) is skipped due to shipment not found in Bol.com'.format(order.name, order.bol_fulfilment_method))
                        mk_log_line_dict['success'].append({'log_message': log_message})
                        continue
                    shipment_data_list = [mk_instance_id._send_bol_request('retailer/shipments/{}'.format(shipment.get('shipmentId')), {}) for shipment in order_shipments]
                    _logger.info(_("IMPORT SHIPMENT: Order# {} ({}) is processing.".format(order.name, order.bol_fulfilment_method)))
                    success_messages, picking_id = self._bol_apply_shipment_data_to_order(order, shipment_data_list, mk_instance_id, mrp_installed=mrp_installed)
                    for success_message in success_messages:
                        mk_log_line_dict['success'].append({'log_message': success_message})
                    # Commit each order's own work immediately (Issue 12): committing only once per
                    # batch of 5 meant one order's exception rolled back the whole batch, discarding
                    # already-successful sibling orders' work even though their "success" messages
                    # were still recorded in mk_log_line_dict and written to mk.log as if nothing
                    # had happened.
                    self.env.cr.commit()
                except OperationalError as e:
                    raise
                except Exception as e:
                    log_traceback_for_exception()
                    self.env.cr.rollback()
                    order.write({'bol_is_skipped_for_import_shipment': True})
                    picking_id and picking_id.write({'is_marketplace_exception': True, 'exception_message': e})
                    self.env.cr.commit()
                    log_message = "IMPORT SHIPMENT: Error while importing shipment for Bol Order {}, ERROR: {}".format(order.name, e)
                    mk_log_line_dict['error'].append({'log_message': log_message})
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_line_dict=mk_log_line_dict)
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
            return True
        return mk_instance_id.action_open_model_view(mk_log_id.ids, 'mk.log', 'Bol.com Shipment Log')

    def cron_auto_import_fbb_shipments(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed' and mk_instance_id.bol_operation_type in ['FBB', 'Both']:
            self.import_bol_shipment_for_open_order(mk_instance_id, fulfillment_type='FBB')
        return True

    def cron_auto_import_fbr_shipments(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed' and mk_instance_id.bol_operation_type in ['FBR', 'Both']:
            self.import_bol_shipment_for_open_order(mk_instance_id, fulfillment_type='FBR')
        return True
