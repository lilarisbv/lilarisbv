import pprint
import logging
import psycopg2
from odoo.http import request
from odoo import api, http, SUPERUSER_ID, _
from odoo.modules.registry import Registry

_logger = logging.getLogger("Teqstars:WooCommerce")


class wooCommerceWebhook(http.Controller):

    @http.route('/woocommerce/webhook/notification/<string:db_name>/<int:mk_instance_id>', type='jsonrpc', auth="public", csrf=False)
    def woocommerce_webhook_process(self, db_name, mk_instance_id, **kwargs):
        webhook_type = request.httprequest.headers.get('X-Wc-Webhook-Topic')
        if webhook_type and webhook_type not in ["product.created", "product.updated", "product.deleted", "order.created", "order.deleted"]:
            return True
        try:
            db_registry = Registry(db_name)
            with db_registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                response = request.get_json_data() or {}
                if 'status' in response and response['status'] == 'checkout-draft':
                    return
                mk_instance_id = env['mk.instance'].browse(int(mk_instance_id))
                if mk_instance_id.state != 'confirmed':
                    return {'status': 'Instance {} is not in Confirmed State.'.format(mk_instance_id.name)}
                mk_log_line_dict = env.context.get('mk_log_line_dict', {'error': [], 'success': []})
                try:
                    mk_log_id = env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='webhook')
                    self.process_webhook_response(env, webhook_type, response, mk_instance_id, mk_log_id)
                except Exception as e:
                    log_message = "Error while processing WooCommerce webhook {}, ERROR: {}.".format(webhook_type, e)
                    mk_log_line_dict['error'].append({'log_message': 'WEBHOOK PROCESS: {}'.format(log_message)})
                finally:
                    env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='webhook', mk_log_line_dict=mk_log_line_dict)
                    if not mk_log_id.log_line_ids:
                        mk_log_id.unlink()
        except psycopg2.Error as e:
            _logger.error(_("WooCommerce WEBHOOK RECEIVE: Error while Processing webhook request. ERROR: {}".format(e)))
        return {'status': 'Successfully processed.'}

    def process_webhook_order(self, webhook_type, response, mk_instance_id):
        if webhook_type == 'product.updated':
            exist = request.env['mk.queue.job.line'].search([('order_id', '=', response.get('id')), ('queue_id.type', '=', 'product'), ('state', 'not in', ['processed', 'cancelled'])])
            exist.write({'data_to_process': pprint.pformat(response)})
            queue_id = exist.queue_id
        else:
            update_product_price = True if webhook_type == 'products.create' else False
            queue_id = mk_instance_id.with_context(update_product_price=update_product_price, update_existing_product=True).action_create_queue(type='product')
            name = response.get('name', '') or ''
            line_vals = {
                'mk_id': response.get('id') or '',
                'state': 'draft',
                'name': name.strip(),
                'data_to_process': pprint.pformat(response),
                'mk_instance_id': mk_instance_id.id,
            }
            queue_id.action_create_queue_lines(line_vals)
        queue_id.do_process()
        queue_id and queue_id.state == 'processed' and queue_id.unlink() or ''
        return True

    def process_webhook_response(self, env, webhook_type, response, mk_instance_id, mk_log_id):
        mk_listing_obj = env['mk.listing']
        mk_log_line_dict = env.context.get('mk_log_line_dict', {'error': [], 'success': []})

        if webhook_type in ["product.created", "product.updated"]:
            mk_listing_obj = env['mk.listing']
            update_product_price = True if webhook_type == 'product.created' else False
            mk_listing_id = mk_listing_obj.with_context(mk_log_line_dict=mk_log_line_dict).create_update_woocommerce_product(response, mk_instance_id,
                                                                                                                             update_product_price=update_product_price)
            if mk_listing_id:
                log_message = "Successfully imported listing {}.".format(mk_listing_id.name, '')
                mk_log_line_dict['success'].append({'log_message': 'IMPORT LISTING: {}'.format(log_message)})

        if webhook_type == 'product.deleted':
            listing_id = mk_listing_obj.search([('mk_id', '=', response.get('id')), ('mk_instance_id', '=', mk_instance_id.id)])
            listing_name = listing_id.name
            listing_id and listing_id.unlink()
            log_message = "Successfully Deleted Listing. Listing Name: {}, Listing ID: {}".format(listing_name, response.get('id'))
            mk_log_line_dict['success'].append({'log_message': 'DELETE PRODUCT: {}'.format(log_message)})

        if webhook_type in ["order.created"]:
            queue_id = mk_instance_id.action_create_queue(type='order')
            name = response.get('number', '') or ''
            line_vals = {
                'mk_id': response.get('id') or '',
                'state': 'draft',
                'name': name.strip(),
                'data_to_process': pprint.pformat(response),
                'mk_instance_id': mk_instance_id.id,
            }
            queue_id.action_create_queue_lines(line_vals)
            queue_id.do_process()
            order_id = queue_id.mk_queue_line_ids.mapped('order_id')
            if order_id:
                log_message = "Successfully imported marketplace order {}.".format(order_id.name, '')
                mk_log_line_dict['success'].append({'log_message': 'IMPORT ORDER: {}'.format(log_message)})
                queue_id and queue_id.unlink() or ''

        if webhook_type == 'order.deleted':
            order = env['sale.order'].search([('mk_id', '=', response.get('id')), ('mk_instance_id', '=', mk_instance_id.id)])
            order_name = order.name
            order and order.unlink()
            log_message = "Successfully Deleted Order. order Name: {}, Order ID: {}".format(order_name, response.get('id'))
            mk_log_line_dict['success'].append({'log_message': 'DELETE ORDER: {}'.format(log_message)})

        env['mk.log'].with_context(operation_type='webhook').create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
        return True
