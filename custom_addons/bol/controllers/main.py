import hmac
import json
import logging
import threading

import psycopg2

from odoo import api, fields, http, SUPERUSER_ID
from odoo.addons.base_marketplace.controllers.main import _safe_open_db_registry
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.addons.base_marketplace.models.misc import resolve_marketplace_db_name
from odoo.addons.bol.models.marketplace_instance import BOL_CONTENT_LANGUAGE_BY_COUNTRY
from odoo.addons.bol.models.misc import log_traceback_for_exception
from odoo.http import request, Response

_logger = logging.getLogger("Teqstars:bol")


class BolWebhook(http.Controller):
    """Receiving endpoint for Bol.com push notification subscriptions.

    Mirrors the Shopify connector: a lightweight ``json`` route that resolves the
    instance from the URL (db / instance id / secret uuid), then hands the work to
    a background thread and returns immediately so Bol always gets a fast response
    (a slow / non-2xx reply makes Bol disable the subscription). Polling crons stay
    active as a fallback. The Bol payload is thin (only the resourceId), so each
    handler fetches the full record via the existing import methods.
    """

    def _bol_json_response(self, data, status=200):
        return Response(json.dumps(data), status=status, content_type='application/json')

    @http.route('/bol/webhook/notification/<string:db_name>/<int:mk_instance_id>/<string:access_token>',
                type='http', auth="public", methods=['POST'], csrf=False, save_session=False)
    def bol_webhook_process(self, db_name, mk_instance_id, access_token, **kwargs):
        raw_body = request.httprequest.get_data() or b''
        try:
            payload = json.loads(raw_body.decode('utf-8')) if raw_body else {}
        except (ValueError, UnicodeDecodeError):
            return self._bol_json_response({'status': 'error', 'message': 'Invalid JSON payload.'}, 400)

        # Task: T9113 - The URL segment is the database name (existing instances) or its token (new instances). Never hand an
        # arbitrary value to Registry(); accept only the database actually serving this request.
        host = request.httprequest.environ.get('HTTP_HOST')
        try:
            resolved_db_name = resolve_marketplace_db_name(db_name, host=host)
        except Exception:
            resolved_db_name = False
        if not resolved_db_name or resolved_db_name != request.db:
            return self._bol_json_response({'status': 'ignored', 'message': 'Invalid database.'})
        db_name = resolved_db_name

        # Validate synchronously so bad token / unconfigured / wrong event are visible in the response.
        instance = request.env['mk.instance'].sudo().browse(int(mk_instance_id))
        event = (payload or {}).get('event') or {}
        resource = event.get('resource')
        validated, message = self._validate_webhook(instance, access_token, resource)
        if not validated:
            # 200 (not 4xx) so Bol does not retry/disable a genuinely-registered subscription;
            # the reason is in the body + server log.
            return self._bol_json_response({'status': 'ignored', 'message': message})

        # Heavy import runs in a thread so Bol gets its 2xx within 5s. Processing errors
        # cannot appear here (response already sent) -> they are logged + written to mk.log.
        webhook_thread = threading.Thread(target=self.create_and_process_webhook_request,
                                          args=(db_name, mk_instance_id, access_token, payload, host))
        webhook_thread.start()
        return self._bol_json_response({'status': 'Successfully processed.'})

    def create_and_process_webhook_request(self, db_name, mk_instance_id, access_token, payload, host=None):
        try:
            # Task: T9113 - Open the registry only if db_name is permitted for this host.
            db_registry = _safe_open_db_registry(db_name, host=host)
            if not db_registry:
                _logger.warning("BOL WEBHOOK RECEIVE: db_name=%s not permitted for host=%s", db_name, host)
                return
            with db_registry.cursor() as cr:
                event = (payload or {}).get('event') or {}
                resource = event.get('resource')
                # One shared, mutable log-line collector lives on the context so every
                # handler + the exception path + the finally flush append to the same dict.
                mk_log_line_dict = {'error': [], 'success': []}
                env = api.Environment(cr, SUPERUSER_ID, {'mk_log_line_dict': mk_log_line_dict})
                mk_instance_id = env['mk.instance'].browse(int(mk_instance_id))

                validated, error_message = self._validate_webhook(mk_instance_id, access_token, resource)
                if not validated:
                    return error_message

                mk_log_id = None
                try:
                    mk_log_id = env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='webhook')
                    self.process_webhook_response(env, resource, event, mk_instance_id, mk_log_id)
                except Exception as e:
                    log_traceback_for_exception()
                    self._handle_exception(env, mk_instance_id, mk_log_id, resource, e)
                finally:
                    # Flush controller-level messages into the same log, then drop it if empty.
                    if mk_log_id and mk_log_id.exists():
                        env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='webhook', mk_log_line_dict=mk_log_line_dict)
                        if not mk_log_id.log_line_ids:
                            mk_log_id.unlink()
        except psycopg2.Error as e:
            _logger.error("BOL WEBHOOK RECEIVE: Error while Processing webhook request. ERROR: {}".format(e))

    def _handle_exception(self, env, mk_instance_id, mk_log_id, resource, exception):
        """Capture a processing error as a visible mk.log line (Shopify-style)."""
        log_message = "Error while processing Bol webhook {}, ERROR: {}.".format(resource, exception)
        _logger.error("BOL WEBHOOK: %s (instance %s)", log_message, mk_instance_id.id)
        mk_log_line_dict = env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        mk_log_line_dict['error'].append({'log_message': 'WEBHOOK PROCESS: {}'.format(log_message)})

    def _validate_webhook(self, mk_instance_id, access_token, resource):
        if not mk_instance_id.exists() or mk_instance_id.marketplace != 'bol':
            return False, "BOL WEBHOOK RECEIVE: Invalid instance."
        # Secret uuid embedded in the URL acts as the shared secret. Reject when it is
        # unset or does not match exactly. Constant-time compare avoids leaking the
        # secret through response-timing, and the explicit blank checks stop a missing
        # token/uuid from ever passing.
        if not mk_instance_id.bol_webhook_uuid or not access_token \
                or not hmac.compare_digest(str(access_token), str(mk_instance_id.bol_webhook_uuid)):
            error_msg = "BOL WEBHOOK RECEIVE: Error while Processing webhook request. ERROR: Invalid Access Token"
            _logger.error(error_msg)
            return False, error_msg
        if mk_instance_id.state != 'confirmed':
            error_msg = 'Instance {} is not ready to receive webhooks.'.format(mk_instance_id.name)
            _logger.warning(error_msg)
            return False, error_msg
        if not mk_instance_id.bol_webhook_ids.filtered(lambda webhook: webhook.webhook_event == resource and webhook.active_webhook):
            error_msg = 'Webhook Event Type {} is not configured in Instance {}.'.format(resource, mk_instance_id.name)
            _logger.warning(error_msg)
            return False, error_msg
        return True, "Success"

    def process_webhook_response(self, env, resource, event, mk_instance_id, mk_log_id):
        resource_id = event.get('resourceId')
        if not resource_id:
            return False
        mk_instance_id.sudo().write({'bol_last_webhook_received': fields.Datetime.now()})
        # Bol push payload is thin (only the resourceId) -> each handler fetches the
        # full record and runs the same idempotent import logic the crons use. One
        # dedicated handler per resource (Shopify-style delegation), adapted to Bol's
        # own per-record processors and mk_log_id logging convention.
        webhook_handlers = {
            'ORDER': lambda: self._handle_bol_order(env, resource_id, mk_instance_id, mk_log_id),
            'SHIPMENT': lambda: self._handle_bol_shipment(env, resource_id, mk_instance_id, mk_log_id),
            'PROCESS_STATUS': lambda: self._handle_bol_process_status(env, resource_id, mk_instance_id, mk_log_id),
            # OFFER_FOR_SALE / OFFER_NOT_FOR_SALE: no webhook support on Bol's side, so these never fire.
            # 'OFFER_FOR_SALE': lambda: self._handle_bol_offer_for_sale(env, resource_id, mk_instance_id, mk_log_id, event),
            # 'OFFER_NOT_FOR_SALE': lambda: self._handle_bol_offer_not_for_sale(env, resource_id, mk_instance_id, mk_log_id, event)
        }
        handler = webhook_handlers.get(resource)
        if handler:
            handler()
        return True

    def _handle_bol_order(self, env, resource_id, mk_instance_id, mk_log_id):
        """ORDER webhook: fetch the order and run the per-record processor.

        Deliberately NOT ``bol_import_order_by_ids`` -- that bulk entry point opens
        its own mk.log, commits the cursor mid-flight and returns an action, none of
        which is safe inside the webhook's shared thread cursor. We mirror its split
        of order items by fulfilment method (FBR/FBB) and call the same idempotent
        ``process_import_order_from_bol_ts`` used per record.
        """
        order_obj = env['sale.order']
        mk_log_line_dict = env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        bol_order_dict = order_obj.fetch_orders_from_bol(mk_instance_id, mk_order_id=resource_id)
        if not isinstance(bol_order_dict, dict) or not bol_order_dict.get('orderItems'):
            mk_log_line_dict['error'].append({'log_message': 'WEBHOOK ORDER: Order {} not found on Bol.com.'.format(resource_id)})
            return False
        fulfilment_types = ['FBR', 'FBB'] if mk_instance_id.bol_operation_type == 'Both' else [mk_instance_id.bol_operation_type]
        for f_type in fulfilment_types:
            order_dict = bol_order_dict.copy()
            order_dict['orderItems'] = [item for item in bol_order_dict.get('orderItems', []) if item.get('fulfilment', {}).get('method') == f_type]
            if order_dict['orderItems']:
                # Savepoint per fulfilment slice so one bad slice does not roll back the other.
                with env.cr.savepoint():
                    order_obj.with_context(operation_type='webhook', mk_log_id=mk_log_id).process_import_order_from_bol_ts(order_dict, mk_instance_id)
        return True

    def _handle_bol_shipment(self, env, resource_id, mk_instance_id, mk_log_id):
        """SHIPMENT webhook: resourceId is the shipmentId; reuse the single-shipment importer."""
        return env['stock.picking'].with_context(operation_type='webhook', mk_log_id=mk_log_id)._process_single_bol_shipment(resource_id, mk_instance_id)

    def _handle_bol_process_status(self, env, resource_id, mk_instance_id, mk_log_id):
        """PROCESS_STATUS webhook: (up)create the local record and refresh its status from Bol."""
        return env['bol.process.status'].process_webhook_event(resource_id, mk_instance_id)

    def _bol_event_is_for_this_country(self, mk_instance_id, event):
        """bol.com reports an offer country by country, so an event about another country says nothing about this instance.

        Only a country bol.com sells in can be compared. An Odoo company is often set up somewhere else entirely, and the
        instance takes its country from that company, so any other country tells us nothing and the event is taken as ours.
        """
        country_code = ((event or {}).get('metadata') or {}).get('countryCode')
        instance_country_code = mk_instance_id.country_id.code
        if not country_code or instance_country_code not in BOL_CONTENT_LANGUAGE_BY_COUNTRY:
            return True
        return country_code == instance_country_code
