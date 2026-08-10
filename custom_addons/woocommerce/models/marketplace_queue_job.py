import pprint
from odoo import models, fields
from odoo.tools.safe_eval import safe_eval
from psycopg2 import OperationalError
from .misc import log_traceback_for_exception


class MkQueueJob(models.Model):
    _inherit = "mk.queue.job"

    woo_update_image = fields.Boolean("Update Image?", help="If True than it will update Image in product template and its Variants.")

    def woocommerce_customer_queue_process(self):
        res_partner_obj, mk_instance_id = self.env['res.partner'], self.mk_instance_id
        draft_queue_line_ids = self.mk_queue_line_ids.filtered(lambda x: x.state == 'draft')
        for line in draft_queue_line_ids:
            customer_dict = safe_eval(line.data_to_process)
            res_partner_obj.with_context(queue_line_id=line, mk_log_id=line.queue_id.mk_log_id).create_update_woocommerce_customers(customer_dict, self.mk_instance_id)
            line.write({'processed_date': fields.Datetime.now()})
        return True

    def woocommerce_order_queue_process(self, draft_queue_line=None):
        sale_order_obj, mk_instance_id = self.env['sale.order'], self.mk_instance_id
        draft_queue_line_ids = draft_queue_line if draft_queue_line else self.mk_queue_line_ids.filtered(lambda x: x.state in ['draft', 'failed'])
        for line in draft_queue_line_ids:
            try:
                woo_order_dict = safe_eval(line.data_to_process)
                with self.env.cr.savepoint():
                    order_id = sale_order_obj.with_context(queue_line_id=line, skip_queue_change_state=True, mk_log_id=line.queue_id.mk_log_id).process_import_order_from_woocommerce_ts(
                        woo_order_dict, mk_instance_id)
            except OperationalError as e:
                self.env.cr.rollback()
            except Exception as e:
                log_traceback_for_exception()
                self.env.cr.rollback()
                log_message = "PROCESS ORDER: Error while processing Marketplace Order {}, ERROR: {}".format(line.mk_id, e)
                self.env['mk.log'].create_update_log(mk_log_id=line.queue_id.mk_log_id, mk_instance_id=mk_instance_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': line and line.id or False}]})
                line.write({'state': 'failed', 'processed_date': fields.Datetime.now()})
            else:
                if order_id:
                    line.write({'state': 'processed', 'processed_date': fields.Datetime.now(), 'order_id': order_id.id if order_id and not isinstance(order_id, bool) else False})
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

    def woocommerce_do_import_listing_process(self, listing_dict, mk_instance_id):
        listing_obj = self.env['mk.listing']
        mk_listing_id = listing_obj.create_update_woocommerce_product(listing_dict, mk_instance_id, update_product_price=True, woo_update_image=self.woo_update_image)
        if not mk_listing_id:
            return False
        return mk_listing_id

    def woocommerce_product_queue_process(self):
        mk_instance_id, queue_job_line_obj = self.mk_instance_id, self.env['mk.queue.job.line']
        draft_queue_line_ids = self.mk_queue_line_ids.filtered(lambda x: x.state == 'draft')
        for line in draft_queue_line_ids:
            product_dict = safe_eval(line.data_to_process)
            mk_listing_id = self.with_context(queue_line_id=line, mk_log_id=line.queue_id.mk_log_id).woocommerce_do_import_listing_process(product_dict, mk_instance_id)
            line.write({'processed_date': fields.Datetime.now(), 'state': 'processed' if mk_listing_id else 'failed', 'mk_listing_id': mk_listing_id and mk_listing_id.id or False})
            self.env.cr.commit()
        if not self.env.context.get('hide_notification', False):
            success_count = self.env['mk.queue.job.line'].search_count([('state', '=', 'processed'), ('id', 'in', draft_queue_line_ids.ids)])
            error_count = self.env['mk.queue.job.line'].search_count([('state', '=', 'failed'), ('id', 'in', draft_queue_line_ids.ids)])
            mk_instance_id.send_smart_notification('is_product_import', 'error', error_count)
            mk_instance_id.send_smart_notification('is_product_import', 'success', success_count)

    def woocommerce_product_retry_failed_queue(self):
        failed_queue_line_ids = self.mk_queue_line_ids.filtered(lambda ql: ql.state == 'failed')
        failed_queue_line_ids and failed_queue_line_ids.woocommerce_product_retry_failed_queue()
        return True

    def woocommerce_order_retry_failed_queue(self):
        failed_queue_line_ids = self.mk_queue_line_ids.filtered(lambda ql: ql.state == 'failed')
        failed_queue_line_ids and failed_queue_line_ids.woocommerce_order_retry_failed_queue()
        return True

    def woocommerce_customer_retry_failed_queue(self):
        failed_queue_line_ids = self.mk_queue_line_ids.filtered(lambda ql: ql.state == 'failed')
        failed_queue_line_ids and failed_queue_line_ids.woocommerce_customer_retry_failed_queue()
        return True


class MkQueueJobLine(models.Model):
    _inherit = "mk.queue.job.line"

    def woocommerce_product_retry_failed_queue(self):
        for line in self.filtered(lambda x: x.mk_id):
            product_dict, status_code = self.mk_instance_id.fetch_data_from_woocommerce('get', 'products/{}'.format(line.mk_id))
            if status_code in [200, 201]:
                line.write({'state': 'draft', 'data_to_process': pprint.pformat(product_dict[0])})
                line.queue_id.with_context(hide_notification=True).woocommerce_product_queue_process()
        return True

    def woocommerce_order_retry_failed_queue(self):
        for line in self.filtered(lambda x: x.mk_id):
            order_dict, status_code = self.mk_instance_id.fetch_data_from_woocommerce('get', 'orders/{}'.format(line.mk_id))
            if status_code in [200, 201]:
                line.write({'state': 'draft', 'data_to_process': pprint.pformat(order_dict[0])})
                line.queue_id.with_context(hide_notification=True).woocommerce_order_queue_process(line)
        return True

    def woocommerce_customer_retry_failed_queue(self):
        for line in self.filtered(lambda x: x.mk_id):
            customer_dict, status_code = self.mk_instance_id.fetch_data_from_woocommerce('get', 'customers/{}'.format(line.mk_id))
            if status_code in [200, 201]:
                line.write({'state': 'draft', 'data_to_process': pprint.pformat(customer_dict[0])})
                line.queue_id.with_context(hide_notification=True).woocommerce_customer_queue_process()
        return True
