import pprint

from psycopg2 import OperationalError

from odoo import models, fields, _
from odoo.tools.safe_eval import safe_eval
from .misc import log_traceback_for_exception


class MkQueueJob(models.Model):
    _inherit = "mk.queue.job"

    def bol_return_queue_process(self):
        bol_return_obj, mk_instance_id = self.env['bol.return'], self.mk_instance_id
        draft_queue_line_ids = self.mk_queue_line_ids.filtered(lambda x: x.state == 'draft')
        for line in draft_queue_line_ids:
            bol_return_dict = safe_eval(line.data_to_process)
            return_success = bol_return_obj.with_context(queue_line_id=line, skip_queue_change_state=True, mk_log_id=line.queue_id.mk_log_id).process_import_return_from_bol_ts(bol_return_dict, mk_instance_id)
            if return_success:
                line.write({'state': 'processed', 'processed_date': fields.Datetime.now()})
            else:
                line.write({'state': 'failed', 'processed_date': fields.Datetime.now()})
            self.env.cr.commit()
        return True

    def bol_order_queue_process(self):
        sale_order_obj, mk_instance_id = self.env['sale.order'], self.mk_instance_id
        draft_queue_line_ids = self.mk_queue_line_ids.filtered(lambda x: x.state in ['draft', 'failed'])
        for line in draft_queue_line_ids:
            try:
                bol_order_dict = safe_eval(line.data_to_process)
                if not bol_order_dict:
                    bol_order_dict = sale_order_obj.fetch_orders_from_bol(mk_instance_id.with_context(from_queue=True), mk_order_id=line.mk_id)
                    if bol_order_dict.get('status') == 404:
                        log_message = _("The order could not be found on Bol.com. This may be due to the order being older than three months or an incorrect order number was provided.")
                        self.env['mk.log'].create_update_log(mk_log_id=self.mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': line.id or False}]})
                        line.write({'state': 'cancelled', 'processed_date': fields.Datetime.now()})
                        continue
                    line.write({'data_to_process': pprint.pformat(bol_order_dict)})
                bol_operation_type = mk_instance_id.bol_get_valid_fulfillment_options()
                order_ids = self.env['sale.order']
                for f_type in bol_operation_type:
                    order_dict = bol_order_dict.copy()
                    order_dict.update({'orderItems': [item for item in order_dict.get('orderItems') if item.get('fulfilment', {}).get('method') == f_type]})
                    if order_dict.get('orderItems'):
                        with self.env.cr.savepoint():
                            order_ids |= sale_order_obj.with_context(queue_line_id=line, skip_queue_change_state=True, mk_log_id=line.queue_id.mk_log_id).process_import_order_from_bol_ts(order_dict, mk_instance_id)
            except OperationalError as e:
                self.env.cr.rollback()
            except Exception as e:
                log_traceback_for_exception()
                self.env.cr.rollback()
                log_message = "PROCESS ORDER: Error while processing Marketplace Order {}, ERROR: {}".format(line.mk_id, e)
                self.env['mk.log'].create_update_log(mk_log_id=line.queue_id.mk_log_id, mk_instance_id=mk_instance_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': line and line.id or False}]})
                line.write({'state': 'failed', 'processed_date': fields.Datetime.now()})
            else:
                if order_ids:
                    line.write({'state': 'processed', 'processed_date': fields.Datetime.now(), 'order_id': order_ids.id if len(order_ids) == 1 else False})
                else:
                    line.write({'state': 'failed', 'processed_date': fields.Datetime.now()})
            self.env.cr.commit()
        if not self.env.context.get('hide_notification', False):
            error_count = self.env['mk.queue.job.line'].search_count([('state', '=', 'failed'), ('id', 'in', draft_queue_line_ids.ids)])
            success_count = self.env['mk.queue.job.line'].search_count([('state', '=', 'processed'), ('id', 'in', draft_queue_line_ids.ids)])
            mk_instance_id.send_smart_notification('is_order_create', 'error', error_count)
            mk_instance_id.send_smart_notification('is_order_create', 'success', success_count)
            if error_count:
                self.create_activity_action("Please check queue job for its fail reason.")

    def bol_product_queue_process(self):
        mk_instance_id, queue_job_line_obj, listing_obj = self.mk_instance_id, self.env['mk.queue.job.line'], self.env['mk.listing']
        draft_queue_line_ids = self.mk_queue_line_ids.filtered(lambda x: x.state == 'draft')
        for line in draft_queue_line_ids:
            bol_product_dict = safe_eval(line.data_to_process)
            if not bol_product_dict:
                bol_product_dict = self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(line.mk_id), {})
                line.write({'data_to_process': pprint.pformat(bol_product_dict)})
            mk_listing_id = listing_obj.search([('mk_instance_id', '=', mk_instance_id.id), ('mk_id', '=', line.mk_id)])

            update_product_price = True if not mk_listing_id else line.queue_id.update_product_price
            is_update_existing_products = True if not mk_listing_id else line.queue_id.update_existing_product
            processed = listing_obj.with_context(queue_line_id=line, mk_log_id=line.queue_id.mk_log_id).create_update_bol_product(bol_product_dict, mk_instance_id, update_product_price=update_product_price,
                                                                                                                                             is_update_existing_products=is_update_existing_products)
            line.write({'processed_date': fields.Datetime.now(), 'state': 'processed' if processed else 'failed'})
            self.env.cr.commit()
        if not self.env.context.get('hide_notification', False):
            success_count = self.env['mk.queue.job.line'].search_count([('state', '=', 'processed'), ('id', 'in', draft_queue_line_ids.ids)])
            error_count = self.env['mk.queue.job.line'].search_count([('state', '=', 'failed'), ('id', 'in', draft_queue_line_ids.ids)])
            mk_instance_id.send_smart_notification('is_product_import', 'error', error_count)
            mk_instance_id.send_smart_notification('is_product_import', 'success', success_count)

    def bol_product_retry_failed_queue(self):
        failed_queue_line_ids = self.mk_queue_line_ids.filtered(lambda ql: ql.state == 'failed')
        failed_queue_line_ids and failed_queue_line_ids.bol_product_retry_failed_queue()
        return True

    def bol_order_retry_failed_queue(self):
        failed_queue_line_ids = self.mk_queue_line_ids.filtered(lambda ql: ql.state == 'failed')
        failed_queue_line_ids and failed_queue_line_ids.bol_order_retry_failed_queue()
        return True

    def bol_return_retry_failed_queue(self):
        failed_queue_line_ids = self.mk_queue_line_ids.filtered(lambda ql: ql.state == 'failed')
        failed_queue_line_ids and failed_queue_line_ids.bol_return_retry_failed_queue()
        return True


class MkQueueJobLine(models.Model):
    _inherit = "mk.queue.job.line"

    def bol_product_retry_failed_queue(self):
        for line in self.filtered(lambda x: x.mk_id):
            offer_response = line.queue_id.mk_instance_id._send_bol_request('retailer/offers/{}'.format(line.mk_id), {})
            line.write({'state': 'draft', 'data_to_process': pprint.pformat(offer_response)})
            line.queue_id.with_context(hide_notification=True).bol_product_queue_process()
        return True

    def bol_order_retry_failed_queue(self):
        for line in self.filtered(lambda x: x.mk_id):
            line.write({'state': 'draft'})
            line.queue_id.with_context(hide_notification=True).bol_order_queue_process()
        return True

    def bol_return_retry_failed_queue(self):
        for line in self.filtered(lambda x: x.mk_id):
            line.write({'state': 'draft'})
            line.queue_id.with_context(hide_notification=True).bol_return_queue_process()
        return True
