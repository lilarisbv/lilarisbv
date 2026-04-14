import csv
import datetime
import logging
import pprint

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import fields, models, api, _
from odoo.tools.misc import split_every
from .misc import convert_bol_datetime_to_utc

_logger = logging.getLogger("TeqStars:bol")

EVENT_TYPES = [
    ('CONFIRM_SHIPMENT', 'CONFIRM_SHIPMENT'),
    ('CREATE_SHIPMENT', 'CREATE_SHIPMENT'),
    ('CANCEL_ORDER', 'CANCEL_ORDER'),
    ('CHANGE_TRANSPORT', 'CHANGE_TRANSPORT'),
    ('HANDLE_RETURN_ITEM', 'HANDLE_RETURN_ITEM'),
    ('CREATE_RETURN_ITEM', 'CREATE_RETURN_ITEM'),
    ('CREATE_INBOUND', 'CREATE_INBOUND'),
    ('DELETE_OFFER', 'DELETE_OFFER'),
    ('CREATE_OFFER', 'CREATE_OFFER'),
    ('UPDATE_OFFER', 'UPDATE_OFFER'),
    ('UPDATE_OFFER_STOCK', 'UPDATE_OFFER_STOCK'),
    ('UPDATE_OFFER_PRICE', 'UPDATE_OFFER_PRICE'),
    ('CREATE_OFFER_EXPORT', 'CREATE_OFFER_EXPORT'),
    ('UNPUBLISHED_OFFER_REPORT', 'UNPUBLISHED_OFFER_REPORT'),
    ('CREATE_PRODUCT_CONTENT', 'CREATE_PRODUCT_CONTENT'),
    ('CREATE_SUBSCRIPTION', 'CREATE_SUBSCRIPTION'),
    ('UPDATE_SUBSCRIPTION', 'UPDATE_SUBSCRIPTION'),
    ('DELETE_SUBSCRIPTION', 'DELETE_SUBSCRIPTION'),
    ('SEND_SUBSCRIPTION_TST_MSG', 'SEND_SUBSCRIPTION_TST_MSG'),
    ('CREATE_SHIPPING_LABEL', 'CREATE_SHIPPING_LABEL'),
    ('CREATE_REPLENISHMENT', 'CREATE_REPLENISHMENT'),
    ('UPDATE_REPLENISHMENT', 'UPDATE_REPLENISHMENT'),
    ('CREATE_CAMPAIGN', 'CREATE_CAMPAIGN'),
    ('UPDATE_CAMPAIGN', 'UPDATE_CAMPAIGN'),
    ('CREATE_AD_GROUP', 'CREATE_AD_GROUP'),
    ('UPDATE_AD_GROUP', 'UPDATE_AD_GROUP'),
    ('CREATE_TARGET_PRODUCT', 'CREATE_TARGET_PRODUCT'),
    ('UPDATE_TARGET_PRODUCT', 'UPDATE_TARGET_PRODUCT'),
    ('CREATE_NEGATIVE_KEYWORD', 'CREATE_NEGATIVE_KEYWORD'),
    ('DELETE_NEGATIVE_KEYWORD', 'DELETE_NEGATIVE_KEYWORD'),
    ('CREATE_KEYWORD', 'CREATE_KEYWORD'),
    ('UPDATE_KEYWORD', 'UPDATE_KEYWORD'),
    ('DELETE_KEYWORD', 'DELETE_KEYWORD'),
    ('REQUEST_PRODUCT_DESTINATIONS', 'REQUEST_PRODUCT_DESTINATIONS'),
    ('CREATE_SOV_SEARCH_TERM_REPORT', 'CREATE_SOV_SEARCH_TERM_REPORT'),
    ('CREATE_SOV_CATEGORY_REPORT', 'CREATE_SOV_CATEGORY_REPORT'),
    ('UPLOAD_INVOICE', 'UPLOAD_INVOICE'),
    ('CREATE_CAMPAIGN_PERFORMANCE_REPORT', 'CREATE_CAMPAIGN_PERFORMANCE_REPORT')
]


class BolProcessStatus(models.Model):
    _name = 'bol.process.status'
    _inherit = ['mail.thread']
    _description = 'Bol Process Status'
    _order = 'id desc'
    _rec_name = 'mk_id'

    mk_id = fields.Char("Marketplace ID", copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')
    entity_id = fields.Char("Entity ID", help='The id of the object being processed. E.g. in case of a shipment process id, you will receive the id of the order item being processed.')
    event_type = fields.Selection(EVENT_TYPES, string='Event Type', help='Name of the requested action that is being processed.')
    description = fields.Text("Description")
    error_message = fields.Text("Error Message", help="Shows error message if applicable.")
    state = fields.Selection(selection=[('pending', 'Pending'), ('success', 'Success'), ('failure', 'Failure'), ('timeout', 'Timeout')], string='Status', default='pending', help="Process Status on Bol")
    user_id = fields.Many2one('res.users', string='Requested By', ondelete='cascade', index=True, default=lambda self: self.env.user)
    mk_create_date = fields.Datetime("Create Date", readonly=True, index=True)
    queue_id = fields.Many2one('mk.queue.job', string="Queue", ondelete='cascade')
    update_existing_product = fields.Boolean("Update Existing Product?", help="If True than it will update listing and listing item.")
    update_product_price = fields.Boolean("Update Price?", help="If True than it will update price in instance's pricelist.")

    def create_or_update_process_status(self, response_dict, instance, update_product_price=False, update_existing_product=False):
        process_id = self.search([('mk_id', '=', response_dict.get('processStatusId')), ('mk_instance_id', '=', instance.id)])
        if process_id:
            process_id.write({
                'state': response_dict.get('status', False) and response_dict.get('status').lower() or False,
                'error_message': response_dict.get('errorMessage'),
            })
            return process_id
        return self.create({
            'mk_id': response_dict.get('processStatusId'),
            'mk_instance_id': instance.id,
            'entity_id': response_dict.get('entityId'),
            'event_type': response_dict.get('eventType'),
            'description': response_dict.get('description'),
            'state': response_dict.get('status', False) and response_dict.get('status').lower() or False,
            'mk_create_date': convert_bol_datetime_to_utc(response_dict.get('createTimestamp')),
            'error_message': response_dict.get('errorMessage'),
            'update_existing_product': update_existing_product,
            'update_product_price': update_product_price,
        })

    def get_process_status(self):
        self.ensure_one()
        try:
            response = self.mk_instance_id._send_bol_request('shared/process-status/{}'.format(self.mk_id), {})
            self.write({
                'entity_id': response.get('entityId'),
                'state': response.get('status', False) and response.get('status').lower() or False,
                'error_message': response.get('errorMessage'),
            })
        except Exception as e:
            self.write({'state': 'failure', 'error_message': e})
        return self.state

    def do_fetch_offer_data(self):
        self.ensure_one()
        if self.state != 'success':
            self.get_process_status()
        listing_obj = self.env['mk.listing']
        if self.state == 'success' and self.entity_id:
            response = self.mk_instance_id._send_bol_request('retailer/offers/export/{}'.format(self.entity_id), {}, accept='application/vnd.retailer.v10+csv')
            reader = csv.reader(response.splitlines(), delimiter=',')
            queue_id = False
            listing_to_delete = listing_obj.search([('mk_instance_id', '=', self.mk_instance_id.id)])
            for index, row in enumerate(reader, start=1):
                if index == 1:
                    continue
                if not queue_id:
                    queue_id = self.mk_instance_id.with_context(update_product_price=self.update_product_price, update_existing_product=self.update_existing_product).action_create_queue(type='product')
                # offer_response = self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(row[0]), {})
                # offer_id = offer_response.get('offerId')
                offer_id = row[0]
                line_vals = {
                    'mk_id': offer_id,
                    'state': 'draft',
                    # 'name': offer_response.get('store', {}).get('productTitle') or offer_response.get('reference'),
                    'name': row[11] or row[1],
                    'data_to_process': pprint.pformat(dict()),
                    'mk_instance_id': self.mk_instance_id.id,
                }
                listing_to_delete -= listing_to_delete.filtered(lambda x: x.mk_id == offer_id)
                queue_id.action_create_queue_lines(line_vals)
            if queue_id:
                msg = _('Offer(s) retrieved and Created a new queue to import: %s') % (queue_id._get_html_link(title=f"#{queue_id.name}"))
            else:
                msg = _('No offer retrieved from Bol')
            self.message_post(body=Markup(msg))
            self.write({'queue_id': queue_id.id if queue_id else False})
            listing_to_delete.unlink()
        return True

    def action_open_queue(self):
        self.ensure_one()
        if not self.queue_id:
            return True
        return {
            'name': "Offer Import Queue : {}".format(self.queue_id.display_name),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mk.queue.job',
            'res_id': self.queue_id.id,
            'target': 'current',
        }

    @api.autovacuum
    def _do_process_status_clean(self):
        try:
            threshold = datetime.datetime.now() - relativedelta(days=10)
            self.env.cr.execute("SELECT id FROM bol_process_status WHERE create_date < %s", (threshold,))
            res = self.env.cr.fetchall()
            process_status_ids = [res_id[0] for res_id in res]
            for process_status_batch in split_every(100, process_status_ids, piece_maker=tuple):
                self.env.cr.execute("""DELETE FROM bol_process_status WHERE id IN %s""", (tuple(process_status_batch),))
        except Exception as e:
            _logger.error('Error while cleaning Process Status : {} '.format(e))
