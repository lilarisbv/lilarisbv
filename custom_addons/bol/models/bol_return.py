import datetime
import logging
import pprint

from odoo import fields, models, api, Command, tools, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT
from .misc import convert_bol_datetime_to_utc

_logger = logging.getLogger("TeqStars:bol")


class BolReturn(models.Model):
    _name = 'bol.return'
    _inherit = ['mail.thread']
    _description = "Bol Returns"
    _rec_name = 'rma_id'
    _order = 'registration_date desc'

    def _compute_is_return_order_created(self):
        for record in self:
            if not record.order_id:
                record.is_return_order_created = True
            elif record.order_id.picking_ids.filtered(lambda x: x.picking_type_id.code == 'incoming' and x.state != 'cancel'):
                record.is_return_order_created = True
            else:
                record.is_return_order_created = False

    mk_id = fields.Char("Marketplace ID", copy=False)
    rma_id = fields.Char('RMA', help='The RMA (Return Merchandise Authorization) id that identifies this particular return.')
    order_id = fields.Many2one('sale.order', string='Order', help="Associated Order")
    product_id = fields.Many2one('product.product', string='Product', help="Associated product")
    partner_id = fields.Many2one('res.partner', string='Customer', related='order_id.partner_id', store=True)
    registration_date = fields.Datetime('Date', help='when this return was registered.')
    expected_quantity = fields.Integer('Expected Quantity', help='The quantity that is expected to be returned by the customer.')
    received_quantity = fields.Integer('Received Quantity', help='The quantity that is received from the customer.')
    return_reason = fields.Char('Return Reason', help='The reason why the customer returned this product.')
    return_reason_comments = fields.Text('Return Reason Comments', help='Additional details from the customer as to why this item was returned.')
    handling_action = fields.Selection(
        [('RETURN_RECEIVED', 'Return Well Received'), ('EXCHANGE_PRODUCT', 'Exchange Item'), ('RETURN_DOES_NOT_MEET_CONDITIONS', 'Item does not meet return conditions'),
         ('REPAIR_PRODUCT', 'Repair Item'),
         ('CUSTOMER_KEEPS_PRODUCT_PAID', 'Customer still keeps items (cancel return)'), ('STILL_APPROVED', 'Still Approved')], string='Handling Action')
    # handled = fields.Boolean('Handled?', help='Indicates if this return item has been handled (by the retailer).')
    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')
    state = fields.Selection([('unhandled', 'To Handle'), ('handled', 'Handled')], default='unhandled', tracking=True)
    processing_result_ids = fields.One2many('bol.return.processing.result', 'bol_return_id', string="Processing Results")
    is_return_order_created = fields.Boolean(string="Return Created?", compute='_compute_is_return_order_created')

    @api.depends('rma_id', 'return_reason')
    def _compute_display_name(self):
        for record in self:
            record.display_name = '{} - {}'.format(record.rma_id, record.return_reason)

    def do_get_status(self):
        self.ensure_one()
        response = self.mk_instance_id._send_bol_request('retailer/returns/{}'.format(self.mk_id), {})
        return self.process_import_return_from_bol_ts(response, self.mk_instance_id)

    def do_handle_return(self):
        self.ensure_one()
        if not self.handling_action:
            raise MarketplaceException(_('Handling Action is mandatory in order to handle this return.'))
        response = self.mk_instance_id._send_bol_request('retailer/returns/{}'.format(self.rma_id), {'handlingResult': self.handling_action, 'quantityReturned': self.received_quantity}, method="PUT")
        process_id = self.env['bol.process.status'].create_or_update_process_status(response, self.mk_instance_id)
        while True:
            process_id.get_process_status()
            if process_id.state == 'success':
                self.write({'state': 'handled'})
                _logger.info("Handle Return: Process Status get Success, marking return as handled in Odoo for RMA ID: {}.".format(self.rma_id))
                break
            if process_id.state in ['failure', 'timeout']:
                raise MarketplaceException(_("Failed to Handle Return '{}'. \nSTATUS: {} ERROR: {}".format(self.name, process_id.state.upper(), process_id.error_message)))
        return True

    def create_return_order(self):
        self.ensure_one()
        product_return_moves = [Command.clear()]
        picking_id = self.order_id.picking_ids.filtered(lambda x: x.picking_type_id.code == 'outgoing' and x.state == 'done')
        if not picking_id:
            raise MarketplaceException(_("There isn't any done delivery order found to create return."))
        if self.received_quantity <= 0:
            raise MarketplaceException(_("Nothing to create return. Please enter received quantity before going for create return."))
        line_fields = list(self.env['stock.return.picking.line']._fields)
        product_return_moves_data_tmpl = self.env['stock.return.picking.line'].default_get(line_fields)
        product_to_return = {self.product_id: self.received_quantity}
        mrp_installed = self.env['ir.module.module'].sudo().search([('name', '=', 'mrp'), ('state', '=', 'installed')])
        if mrp_installed:
            bom = self.env['mrp.bom'].sudo()._bom_find(products=self.product_id, company_id=self.order_id.company_id.id, bom_type='phantom')[self.product_id]
            if bom:
                factor = self.product_id.uom_id._compute_quantity(self.received_quantity, bom.product_uom_id) / bom.product_qty
                boms, lines = bom.sudo().explode(self.product_id, factor, picking_type=bom.picking_type_id)
                for bom_line, line_data in lines:
                    product_to_return.update({bom_line.product_id: line_data['qty']})
        for move in picking_id.move_ids:
            if not product_to_return.get(move.product_id):
                continue
            if move.state == 'cancel' or move.location_dest_usage == 'inventory':
                continue
            product_return_moves_data = dict(product_return_moves_data_tmpl)
            product_return_moves_data.update(self.env['stock.return.picking']._prepare_stock_return_picking_line_vals_from_move(move))
            product_return_moves_data.update({'quantity': product_to_return.get(move.product_id)})
            product_return_moves.append(Command.create(product_return_moves_data))
        if picking_id and not product_return_moves:
            raise MarketplaceException(_("No products to return (only lines in Done state and not fully returned yet can be returned)."))
        if picking_id:
            return_record = self.env['stock.return.picking'].with_context(active_id=picking_id.id, active_model='stock.picking').create(
                {'picking_id': picking_id.id, 'product_return_moves': product_return_moves})
            new_picking = return_record._create_return()
            if new_picking:
                new_picking.write({'mk_instance_id': picking_id.mk_instance_id.id, 'origin': '{} ({})'.format(new_picking.origin, picking_id.group_id.name)})
            # new_picking.button_validate()
            return new_picking
        return False

    def action_open_picking_from_return(self):
        return {
            'name': "Pickings",
            'view_mode': 'list,form',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.order_id.picking_ids.ids)]
        }

    def fetch_returns_from_bol(self, mk_instance_id, type="FBR", handled=False, from_date=False):
        bol_return_list, next_page, page = [], True, 0
        while next_page:
            page += 1
            response = mk_instance_id._send_bol_request('retailer/returns', {}, params={'fulfilment-method': type, 'handled': str(handled), 'page': page})
            if not response.get('returns'):
                break
            last_sync_date = from_date or mk_instance_id.bol_last_return_sync_on
            if not last_sync_date:
                last_sync_date = datetime.datetime.now() - datetime.timedelta(days=3)
            for return_dict in response.get('returns'):
                return_date = convert_bol_datetime_to_utc(return_dict.get('registrationDateTime'))
                return_date = datetime.datetime.strptime(return_date, DEFAULT_SERVER_DATETIME_FORMAT)
                if handled and last_sync_date > return_date:
                    next_page = False
                    break
                if not handled and last_sync_date > return_date:
                    continue
                bol_return_list.append(return_dict)
        return bol_return_list

    def _prepare_processing_result_line_vals(self, processing_results_list):
        lines = []
        if processing_results_list is not None:
            for presult in processing_results_list:
                lines.append(
                    (0, 0, {'quantity': presult.get('quantity'), 'processing_result': presult.get('processingResult'), 'handling_result': presult.get('handlingResult'),
                            'date': convert_bol_datetime_to_utc(presult.get('processingDateTime'))}))
        return lines

    def process_import_return_from_bol_ts(self, bol_return_dict, mk_instance_id):
        bol_return = self.env['bol.return']
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        return_id = bol_return_dict.get('returnId')
        return_date = bol_return_dict.get('registrationDateTime')
        for return_item in bol_return_dict.get('returnItems'):
            rma_id = return_item.get('rmaId')
            existing_bol_return_id = self.search([('rma_id', '=', rma_id), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            processing_line_vals = self._prepare_processing_result_line_vals(return_item.get('processingResults'))
            if existing_bol_return_id:
                existing_bol_return_id.processing_result_ids.unlink()
                existing_bol_return_id.write({
                    'state': 'handled' if return_item.get('handled') else 'unhandled',
                    'processing_result_ids': processing_line_vals,
                })
                continue
            order_id = self.env['sale.order'].search([('mk_id', '=', return_item.get('orderId')), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            # order_id = self.env['sale.order'].search_read([('mk_id', '=', return_item.get('orderId')), ('mk_instance_id', '=', mk_instance_id.id)], ['id'], limit=1)
            if not order_id:
                log_message = _('IMPORT RETURN : Order not found for RMA# {} (Order : {})'.format(rma_id, return_item.get('orderId')))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                return False
            product_id = self.env['product.product'].search([('barcode', '=', return_item.get('ean'))], limit=1)
            if not product_id:
                log_message = _('IMPORT RETURN : Odoo Product not found for EAN {}, RMA# {} (Order : {})'.format(return_item.get('ean'), rma_id, return_item.get('orderId')))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                return False
            return_reason_comments = return_item.get('returnReason', {}).get('detailedReason', '')
            if return_item.get('returnReason', {}).get('customerComments', ''):
                return_reason_comments += '\n' + return_item.get('returnReason', {}).get('customerComments', '')
            bol_return |= self.create({
                'mk_id': return_id,
                'rma_id': rma_id,
                'order_id': order_id.id,
                'product_id': product_id.id,
                'registration_date': convert_bol_datetime_to_utc(return_date),
                'expected_quantity': return_item.get('expectedQuantity'),
                'return_reason': return_item.get('returnReason', {}).get('mainReason'),
                'return_reason_comments': return_reason_comments,
                'mk_instance_id': mk_instance_id.id,
                'state': 'handled' if return_item.get('handled') else 'unhandled',
                'processing_result_ids': processing_line_vals,
            })
            log_message = _('IMPORT RETURN : RMA# {} ({}) successfully created'.format(rma_id, return_id))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        return bol_return

    def bol_import_returns(self, mk_instance_id, from_date=False):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
        valid_fulfillment_options = mk_instance_id.bol_get_valid_fulfillment_options()
        last_return_import_date = fields.Datetime.now()
        for fulfillment_option in valid_fulfillment_options:
            bol_return_list = self.fetch_returns_from_bol(mk_instance_id, type=fulfillment_option, handled=(mk_instance_id.bol_import_return == 'handled'), from_date=from_date)
            if bol_return_list:
                batch_size = mk_instance_id.queue_batch_limit or 100
                for bol_returns in tools.split_every(batch_size, bol_return_list):
                    queue_id = mk_instance_id.action_create_queue(type='return')
                    for return_dict in bol_returns:
                        return_id = return_dict.get('returnId')
                        # Not skipping return if found already because it may be possible that return was imported as unhandled and then
                        # from bol.com customer processed/handled it so, we have to make it handled in Odoo.
                        # if self.search_count([('mk_id', '=', return_id), ('mk_instance_id', '=', mk_instance_id.id)]):
                        #     _logger.info(_("IMPORT RETURN: Return# {} ({}) is already imported.".format(return_id, fulfillment_option)))
                        #     continue
                        line_vals = {
                            'mk_id': return_id,
                            'state': 'draft',
                            'name': ', '.join([str(return_item.get('rmaId', '')) for return_item in return_dict.get('returnItems')]),
                            'data_to_process': pprint.pformat(return_dict),
                            'mk_instance_id': mk_instance_id.id,
                        }
                        queue_id.action_create_queue_lines(line_vals)
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        mk_instance_id.bol_last_return_sync_on = last_return_import_date
        return True

    def cron_import_returns(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.bol_import_returns(mk_instance_id)
        return True


class BolReturnProcessingResult(models.Model):
    _name = 'bol.return.processing.result'
    _description = "Processing Result"

    date = fields.Datetime('Date', help='when this return was registered.')
    quantity = fields.Integer('Quantity', help='The processed quantity.')
    processing_result = fields.Char('Processing Result', help='The processing result of the return.')
    handling_result = fields.Char('Handling Result', help='The handling result requested by the retailer.')
    bol_return_id = fields.Many2one('bol.return', string="Return", help="Associated return", ondelete='cascade')
