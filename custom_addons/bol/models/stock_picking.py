import logging
import pprint
import time
from itertools import zip_longest

from psycopg2 import OperationalError

from odoo import fields, models, tools, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.tools import float_is_zero
from .misc import log_traceback_for_exception

_logger = logging.getLogger("TeqStars:bol")

BOL_FULFILMENT_METHOD = [('FBB', 'Fulfilment by bol.com'), ('FBR', 'Fulfilment by retailer')]


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    bol_fulfilment_method = fields.Selection(BOL_FULFILMENT_METHOD, string='Fulfilment Method (Bol)', help='Specifies whether this shipment has been fulfilled by the retailer (FBR) or fulfilled by bol.com (FBB). Defaults to FBR.')
    bol_shipment_numbers = fields.Char("Bol Shipment(s)", help="Shipment identification")

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
            order_response = mk_instance_id._send_bol_request('retailer/orders/{}'.format(order.mk_id), {})
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

    def do_bol_update_order_status(self, manual_process=False):
        mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        for picking in self:
            order_id = picking.sale_id
            mk_instance_id = self.mk_instance_id
            bol_shipments = mk_instance_id._send_bol_request('retailer/shipments?order-id={}'.format(order_id.mk_id), {})
            bol_transporter_code = picking.carrier_id.bol_transporter_id.code
            if not bol_transporter_code:
                log_message = _('Bol Transporter must be set in carrier/shipping method {}'.format(picking.carrier_id.name))
                if manual_process:
                    raise MarketplaceException(log_message)
                else:
                    not manual_process and mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: Bol order {}, ERROR: {}.'.format(order_id.name, log_message)})
                    continue
            if any([line.product_id.type != 'service' and not line.mk_id for line in order_id.order_line]):
                log_message = _('Cannot update order status because Bol Order Line ID not found in Order {}'.format(order_id.name))
                if manual_process:
                    raise MarketplaceException(log_message)
                else:
                    not manual_process and mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                    continue
            order_items_ids = set([])
            for move in picking.move_ids:
                bol_order_item_id = move.sale_line_id.mk_id or False
                if bol_order_item_id:
                    already_updated_in_bol = False
                    for shipment in bol_shipments.get('shipments', []):
                        for shipment_item in shipment.get("shipmentItems"):
                            if shipment_item.get("orderItemId") == bol_order_item_id:
                                already_updated_in_bol = True
                                break
                    if not already_updated_in_bol:
                        order_items_ids.add(bol_order_item_id)
            if order_items_ids:
                try:
                    order_items_ids = list(order_items_ids)
                    tracking_list = picking.carrier_tracking_ref and picking.carrier_tracking_ref.split(',') or ['']
                    zipped_items = zip_longest(order_items_ids, tracking_list, fillvalue=tracking_list[-1] if len(tracking_list) > 1 else tracking_list[0])
                    for item_id, tracking_number in zipped_items:
                        request_dict = {'orderItems': [{'orderItemId': item_id}],
                                        'shipmentReference': picking.name,
                                        'transport': {'transporterCode': bol_transporter_code,
                                                      'trackAndTrace': tracking_number}}
                        response = mk_instance_id._send_bol_request('retailer/shipments'.format(bol_order_item_id), request_dict, method="POST")
                        process_id = self.env['bol.process.status'].create_or_update_process_status(response, mk_instance_id)
                        while True:
                            process_id.get_process_status()
                            if process_id.state == 'success' and process_id.entity_id:
                                break
                            if process_id.state in ['failure', 'timeout']:
                                log_message = _("Failed to fulfill order '{}' in Bol.com. \nSTATUS: {} \nERROR: {}".format(order_id.name, process_id.state.upper(), process_id.error_message))
                                if manual_process:
                                    raise MarketplaceException(log_message)
                                else:
                                    picking.write({'is_marketplace_exception': True, 'exception_message': log_message})
                                    mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                                    break
                        time.sleep(8)
                except Exception as e:
                    if manual_process:
                        raise MarketplaceException(e, additional_context={'show_traceback': True})
                    else:
                        picking.write({'is_marketplace_exception': True, 'exception_message': e})
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: Bol order {}, ERROR: {}.'.format(order_id.name, e)})
            bol_shipments = mk_instance_id._send_bol_request('retailer/shipments?order-id={}'.format(order_id.mk_id), {})
            if bol_shipments:
                picking.write({'updated_in_marketplace': True, 'is_marketplace_exception': False, 'exception_message': False, 'bol_shipment_numbers': ','.join([str(shipment.get('shipmentId')) for shipment in bol_shipments.get('shipments')])})
                not manual_process and mk_log_line_dict['success'].append({'log_message': 'UPDATE ORDER STATUS: Successfully updated tracking information on Bol.com for order {}'.format(order_id.name)})
            self.env.cr.commit()
        return True

    def bol_update_order_status_to_marketplace(self):
        self.filtered(lambda x: x.mk_instance_id.marketplace == 'bol').do_bol_update_order_status(manual_process=True)
        return True

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

    def import_bol_shipment_for_open_order(self, mk_instance_id, fulfillment_type=False):
        mk_log_line_dict= {'error': [], 'success': []}
        order_ids = self.get_open_order_for_bol(mk_instance_id, fulfillment_type=fulfillment_type)
        mrp_installed = self.env['ir.module.module'].sudo().search([('name', '=', 'mrp'), ('state', '=', 'installed')])
        valid_fulfillment_options = list(set(order_ids.mapped('bol_fulfilment_method')))
        bol_shipment_dict = {fulfilment_method : {} for fulfilment_method in valid_fulfillment_options}
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
                    carrier_wise_shipment_dict = {}
                    for shipment in order_shipments:
                        shipment_data = mk_instance_id._send_bol_request('retailer/shipments/{}'.format(shipment.get('shipmentId')), {})
                        transporter_code = shipment_data.get('transport', {}).get('transporterCode', '')
                        if transporter_code in carrier_wise_shipment_dict:
                            carrier_wise_shipment_dict[transporter_code].append(shipment_data)
                        else:
                            carrier_wise_shipment_dict[transporter_code] = [shipment_data]
                    _logger.info(_("IMPORT SHIPMENT: Order# {} ({}) is processing.".format(order.name, order.bol_fulfilment_method)))
                    precision_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
                    bol_shipment_numbers = set([])
                    for code, shipments in carrier_wise_shipment_dict.items():
                        picking_id = order.picking_ids.filtered(lambda x: x.state not in ['done', 'cancel'])
                        carrier_tracking_set = set([])
                        for shipment in shipments:
                            for shipment_item in shipment.get('shipmentItems'):
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
                                        factor = product_id.uom_id._compute_quantity(float(shipment_item.get('quantity')), bom.product_uom_id) / bom.product_qty
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
                                record = self.env[res.get('res_model')].with_context(res.get("context")).create({})
                                record.process()
                        if picking_id.state == 'done':
                            picking_id.write({'updated_in_marketplace': True, 'is_marketplace_exception': False, 'exception_message': False, 'bol_shipment_numbers': ','.join(list(bol_shipment_numbers))})
                            log_message = _('IMPORT SHIPMENT : Order# {} ({}) successfully shipped in Odoo.'.format(order.name, order.bol_fulfilment_method))
                            mk_log_line_dict['success'].append({'log_message': log_message})
                except OperationalError as e:
                    raise
                except Exception as e:
                    log_traceback_for_exception()
                    self.env.cr.rollback()
                    order.write({'bol_is_skipped_for_import_shipment': True})
                    picking_id and picking_id.write({'is_marketplace_exception': True, 'exception_message': e})
                    log_message = "IMPORT SHIPMENT: Error while importing shipment for Bol Order {}, ERROR: {}".format(order.name, e)
                    mk_log_line_dict['error'].append({'log_message': log_message})
            self.env.cr.commit()
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_line_dict=mk_log_line_dict)
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        return True

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
