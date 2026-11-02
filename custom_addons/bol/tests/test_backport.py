import datetime
import json
from unittest.mock import MagicMock, patch

import requests

from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.addons.bol.models import bol_process_status
from odoo.tests.common import TransactionCase


def fake_response(payload=None, status_code=200, headers=None):
    """A requests.Response: _send_bol_request reads .text, never .json(), except in its error branch."""
    response = MagicMock(status_code=status_code, reason='OK' if status_code < 400 else 'Error', headers=headers or {})
    response.text = json.dumps(payload) if payload is not None else ''
    response.json.return_value = payload
    if status_code >= 400:
        def _raise(_response=response):
            raise requests.HTTPError(response=_response)
        response.raise_for_status.side_effect = _raise
    else:
        response.raise_for_status.return_value = None
    return response


class BolBackportCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mk_instance = cls.env['mk.instance'].create({
            'name': 'Bol Backport Instance', 'marketplace': 'bol', 'bol_client_id': 'test-client-id', 'bol_client_secret': 'test-client-secret',
            'bol_access_token': 'preset-test-token', 'bol_expires_in': '99999999',
        })
        cls.mk_instance.bol_last_token_generated_on = datetime.datetime.now()
        cls.mk_instance.set_pricelist('EUR')


class TestRetryAfter(BolBackportCommon):
    """Item 3: a 429 waits as long as Bol.com asks (Retry-After) and gives up after a few tries, no fixed 60 seconds."""

    def test_wait_follows_the_header(self):
        wait = self.env['mk.instance']._bol_retry_wait
        self.assertEqual(wait(MagicMock(headers={'Retry-After': '5'})), 5.0)
        self.assertEqual(wait(MagicMock(headers={'Retry-After': '0'})), 0.0)

    def test_wait_is_capped_and_falls_back_to_60(self):
        wait = self.env['mk.instance']._bol_retry_wait
        self.assertEqual(wait(MagicMock(headers={'Retry-After': '500'})), 60)
        self.assertEqual(wait(MagicMock(headers={})), 60)
        self.assertEqual(wait(MagicMock(headers={'Retry-After': 'tomorrow'})), 60)

    def test_429_is_retried_with_the_header_wait_and_then_succeeds(self):
        answers = [fake_response({'detail': 'Too many requests'}, 429, {'Retry-After': '3'}), fake_response({'ok': 'yes'})]
        with patch('requests.request', side_effect=answers) as request, patch('time.sleep') as sleep:
            result = self.mk_instance._send_bol_request('retailer/orders', {})
        self.assertEqual(result, {'ok': 'yes'})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(3.0)

    def test_429_gives_up_after_three_retries(self):
        answers = [fake_response({'detail': 'Too many requests'}, 429, {'Retry-After': '1'}) for dummy in range(4)]
        with patch('requests.request', side_effect=answers) as request, patch('time.sleep') as sleep:
            with self.assertRaises(MarketplaceException):
                self.mk_instance._send_bol_request('retailer/orders', {})
        self.assertEqual(request.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_a_normal_error_is_not_retried(self):
        with patch('requests.request', side_effect=[fake_response({'detail': 'Not allowed'}, 403)]) as request, patch('time.sleep') as sleep:
            with self.assertRaises(MarketplaceException):
                self.mk_instance._send_bol_request('retailer/orders', {})
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()


class TestInvoiceRequestPaging(BolBackportCommon):
    """Item 1: open invoice requests are read page by page, 50 on a page."""

    def _run(self, pages):
        calls = []

        def answer(self_, url, data, params=None, **kwargs):
            calls.append(dict(params))
            return {'invoiceRequests': pages[len(calls) - 1]}
        with patch.object(type(self.mk_instance), '_send_bol_request', answer), \
                patch.object(type(self.env['bol.invoice.request']), '_sync_open_invoice_requests_status', return_value=True):
            self.env['bol.invoice.request'].get_open_import_request_for_invoice(self.mk_instance)
        return calls

    def test_one_short_page_is_one_request(self):
        calls = self._run([[{}] * 3])
        self.assertEqual(calls, [{'state': 'OPEN', 'page': 1}])

    def test_a_full_page_asks_for_the_next_page(self):
        calls = self._run([[{}] * 50, [{}] * 50, [{}] * 7])
        self.assertEqual([call['page'] for call in calls], [1, 2, 3])

    def test_an_empty_answer_stops(self):
        calls = self._run([[]])
        self.assertEqual(len(calls), 1)

    def test_every_page_is_imported(self):
        pages = [[{'orderId': 'PG-%03d' % index, 'shipmentId': 'SH-%03d' % index} for index in range(50)],
                 [{'orderId': 'PG-%03d' % index, 'shipmentId': 'SH-%03d' % index} for index in range(50, 53)]]
        self._run(pages)
        self.assertEqual(self.env['bol.invoice.request'].search_count([('order_id', 'like', 'PG-'), ('mk_instance_id', '=', self.mk_instance.id)]), 53)


class TestWaitForResult(BolBackportCommon):
    """Items 6, 7, 8: the process status is checked a limited number of times, no endless loop."""

    def _status(self, name='ps-1'):
        return self.env['bol.process.status'].create_or_update_process_status(
            {'processStatusId': name, 'status': 'PENDING', 'eventType': 'CREATE_SHIPMENT'}, self.mk_instance)

    def _wait(self, states, require_entity=False):
        """states: what each check finds, as (state, entityId)."""
        process = self._status()
        answers = iter(states)

        def check(record):
            state, entity = next(answers, (states[-1]))
            record.write({'state': state, 'entity_id': entity})
            return record.state
        with patch.object(type(process), 'get_process_status', check), patch('time.sleep') as sleep:
            result = process.bol_wait_for_result(require_entity=require_entity)
        return result, sleep

    def test_success_at_the_first_check(self):
        result, sleep = self._wait([('success', 'E1')])
        self.assertEqual(result, 'success')
        sleep.assert_not_called()

    def test_waits_longer_each_time_until_success(self):
        result, sleep = self._wait([('pending', False), ('pending', False), ('success', 'E1')])
        self.assertEqual(result, 'success')
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_failure_stops_at_once(self):
        result, sleep = self._wait([('pending', False), ('failure', False)])
        self.assertEqual(result, 'failure')
        self.assertEqual(sleep.call_count, 1)

    def test_timeout_stops_at_once(self):
        self.assertEqual(self._wait([('timeout', False)])[0], 'timeout')

    def test_still_pending_ends_after_the_last_check(self):
        result, sleep = self._wait([('pending', False)])
        self.assertEqual(result, 'pending')
        self.assertEqual(sleep.call_count, bol_process_status.BOL_PROCESS_STATUS_MAX_CHECKS - 1)

    def test_the_wait_between_checks_never_goes_over_30_seconds(self):
        result, sleep = self._wait([('pending', False)])
        self.assertLessEqual(max(call.args[0] for call in sleep.call_args_list), 30)

    def test_success_without_entity_id_is_not_enough_when_it_is_required(self):
        self.assertEqual(self._wait([('success', False)], require_entity=True)[0], 'pending')
        self.assertEqual(self._wait([('success', False)], require_entity=False)[0], 'success')


class TestHandleReturnAndCancel(BolBackportCommon):

    def _return(self):
        return self.env['bol.return'].create({'rma_id': 'RMA-1', 'mk_instance_id': self.mk_instance.id, 'handling_action': 'RETURN_RECEIVED', 'received_quantity': 1})

    def _handle(self, result_state):
        bol_return = self._return()
        with patch.object(type(self.mk_instance), '_send_bol_request', return_value={'processStatusId': 'ps-r', 'status': 'PENDING', 'eventType': 'HANDLE_RETURN_ITEM'}), \
                patch.object(type(self.env['bol.process.status']), 'bol_wait_for_result', return_value=result_state):
            if result_state == 'success':
                bol_return.do_handle_return()
            else:
                with self.assertRaises(MarketplaceException) as caught:
                    bol_return.do_handle_return()
                return bol_return, str(caught.exception)
        return bol_return, ''

    def test_return_is_handled_on_success(self):
        bol_return, dummy = self._handle('success')
        self.assertEqual(bol_return.state, 'handled')

    def test_return_stays_open_when_bol_fails(self):
        bol_return, message = self._handle('failure')
        self.assertEqual(bol_return.state, 'unhandled')
        self.assertIn('Failed to Handle Return', message)

    def test_return_stays_open_when_bol_is_not_finished(self):
        bol_return, message = self._handle('pending')
        self.assertEqual(bol_return.state, 'unhandled')
        self.assertIn('has not finished', message)

    def _cancel(self, result_state):
        partner = self.env['res.partner'].create({'name': 'Cancel Customer'})
        product = self.env['product.product'].create({'name': 'Cancel Product', 'type': 'consu', 'list_price': 5.0})
        order = self.env['sale.order'].create({'partner_id': partner.id, 'mk_instance_id': self.mk_instance.id, 'mk_id': 'CANCEL-1'})
        line = self.env['sale.order.line'].create({'order_id': order.id, 'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 5.0, 'mk_id': 'ITEM-1'})
        wizard = self.env['mk.cancel.order'].with_context(active_id=order.id).create({
            'bol_cancel_item_line_ids': [(0, 0, {'order_line_id': line.id, 'bol_cancel_reason': 'OUT_OF_STOCK'})]})
        with patch.object(type(self.mk_instance), '_send_bol_request', return_value={'processStatusId': 'ps-c', 'status': 'PENDING', 'eventType': 'CANCEL_ORDER'}), \
                patch.object(type(self.env['bol.process.status']), 'bol_wait_for_result', return_value=result_state), \
                patch.object(self.env.cr, 'commit'):
            if result_state == 'success':
                wizard.do_cancel_in_bol()
                return order, ''
            with self.assertRaises(MarketplaceException) as caught:
                wizard.do_cancel_in_bol()
        return order, str(caught.exception)

    def test_cancel_succeeds(self):
        order, dummy = self._cancel('success')
        self.assertTrue(order.canceled_in_marketplace)

    def test_cancel_failure_is_reported(self):
        order, message = self._cancel('failure')
        self.assertFalse(order.canceled_in_marketplace)
        self.assertIn('Failed to Cancel Order', message)

    def test_cancel_not_finished_is_reported_and_nothing_changes(self):
        order, message = self._cancel('pending')
        self.assertFalse(order.canceled_in_marketplace)
        self.assertIn('has not finished', message)


class TestListingSyncButtons(BolBackportCommon):
    """Item 9: 'Sync to Odoo' and 'Sync to Bol.com' on the listing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mk_instance.state = 'confirmed'
        cls.product = cls.env['product.product'].create({'name': 'Sync Product', 'type': 'consu', 'list_price': 12.5, 'barcode': '9900000000048', 'default_code': 'SYNC-1'})
        cls.listing = cls.env['mk.listing'].create({
            'name': cls.product.name, 'mk_instance_id': cls.mk_instance.id, 'product_tmpl_id': cls.product.product_tmpl_id.id,
            'mk_id': 'sync-offer', 'is_listed': True, 'bol_fulfilment_method': 'FBB'})
        cls.env['mk.listing.item'].create({
            'name': cls.product.name, 'product_id': cls.product.id, 'mk_listing_id': cls.listing.id, 'mk_id': 'sync-offer',
            'mk_instance_id': cls.mk_instance.id, 'is_listed': True})

    def test_toggle_switches_the_sync_off_and_on(self):
        self.assertFalse(self.listing.bol_skip_listing_sync)
        self.listing.bol_toggle_listing_sync()
        self.assertTrue(self.listing.bol_skip_listing_sync)
        self.listing.bol_toggle_listing_sync()
        self.assertFalse(self.listing.bol_skip_listing_sync)

    def test_sync_is_refused_when_switched_off(self):
        self.listing.bol_skip_listing_sync = True
        with patch.object(type(self.mk_instance), '_send_bol_request') as request:
            for button in (self.listing.bol_sync_listing_to_bol, self.listing.bol_sync_listing_to_odoo):
                with self.assertRaises(MarketplaceException):
                    button()
        request.assert_not_called()
        self.listing.bol_skip_listing_sync = False

    def test_sync_is_refused_for_an_instance_that_is_not_confirmed(self):
        self.mk_instance.state = 'draft'
        try:
            with self.assertRaises(MarketplaceException):
                self.listing.bol_sync_listing_to_bol()
        finally:
            self.mk_instance.state = 'confirmed'

    def test_sync_to_bol_sends_one_patch_with_fulfilment_and_price(self):
        with patch.object(type(self.mk_instance), '_send_bol_request', return_value=True) as request, \
                patch.object(type(self.listing), '_bol_fetch_not_for_sale_reasons'):
            self.listing.bol_sync_listing_to_bol()
        self.assertEqual(request.call_count, 1)
        args, kwargs = request.call_args
        self.assertEqual(args[0], 'retailer/offers/sync-offer')
        self.assertEqual(kwargs['method'], 'PATCH')
        body = args[1]
        self.assertEqual(body['fulfilment'], {'method': 'FBB'})
        self.assertEqual(body['pricing']['bundlePrices'][0]['quantity'], 1)
        self.assertGreater(body['pricing']['bundlePrices'][0]['unitPrice'], 0)

    def test_sync_to_odoo_reads_the_offer_and_updates_the_listing(self):
        with patch.object(type(self.mk_instance), '_send_bol_request', return_value={'offerId': 'sync-offer'}) as request, \
                patch.object(type(self.listing), 'create_update_bol_product', return_value=True) as update:
            self.listing.bol_sync_listing_to_odoo()
        self.assertEqual(request.call_args.args[0], 'retailer/offers/sync-offer')
        self.assertEqual(request.call_args.kwargs['method'], 'GET')
        update.assert_called_once()
        self.assertTrue(update.call_args.kwargs.get('update_product_price') or update.call_args.args[2:])


class TestPendingProcessStatusCron(BolBackportCommon):
    """The scheduled action 'Bol.com : Check Pending Process Status': a safety net that reads pending statuses again (no webhook, slow Bol.com)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mk_instance.state = 'confirmed'

    def _status(self, name, instance=None):
        return self.env['bol.process.status'].create({'mk_id': name, 'mk_instance_id': (instance or self.mk_instance).id, 'state': 'pending', 'event_type': 'CREATE_SHIPMENT'})

    def _run(self, answer, **cron_kwargs):
        """answer: what Bol.com says to every status read (a dict), or an exception to raise."""
        kwargs = {'side_effect': answer} if isinstance(answer, Exception) else {'return_value': answer}
        with patch.object(type(self.mk_instance), '_send_bol_request', **kwargs) as request:
            self.env['bol.process.status'].cron_check_pending_process_status(**cron_kwargs)
        return request

    def test_the_scheduled_action_exists_and_runs_every_15_minutes(self):
        cron = self.env.ref('bol.bol_check_pending_process_status')
        self.assertTrue(cron.active)
        self.assertEqual((cron.interval_number, cron.interval_type), (15, 'minutes'))
        self.assertEqual(cron.code, 'model.cron_check_pending_process_status()')

    def test_a_pending_status_that_succeeded_is_updated(self):
        status = self._status('cron-1')
        request = self._run({'status': 'SUCCESS', 'entityId': 'E9'})
        self.assertEqual((status.state, status.entity_id), ('success', 'E9'))
        self.assertEqual(request.call_args.args[0], 'shared/process-status/cron-1')

    def test_a_failure_answer_is_kept_as_a_failure_with_its_reason(self):
        status = self._status('cron-2')
        answer = fake_response({'processStatusId': 'cron-2', 'status': 'FAILURE', 'errorMessage': 'Invalid offer'})
        with patch('requests.request', return_value=answer):
            self.env['bol.process.status'].cron_check_pending_process_status()
        self.assertEqual((status.state, status.error_message), ('failure', 'Invalid offer'))

    def test_a_failure_answer_still_raises_for_every_other_request(self):
        answer = fake_response({'processStatusId': 'x', 'status': 'FAILURE', 'errorMessage': 'Invalid offer'})
        with patch('requests.request', return_value=answer):
            with self.assertRaises(MarketplaceException):
                self.mk_instance._send_bol_request('shared/process-status/x', {})

    def test_a_network_error_keeps_the_status_pending_and_skips_the_rest_of_that_instance(self):
        first, second = self._status('cron-3a'), self._status('cron-3b')
        request = self._run(requests.ConnectionError('no network'))
        self.assertEqual((first.state, second.state), ('pending', 'pending'))
        self.assertEqual(request.call_count, 1)

    def test_it_gives_up_after_one_day_without_asking_bol(self):
        status = self._status('cron-4')
        self.env.cr.execute("UPDATE bol_process_status SET create_date = %s WHERE id = %s", (datetime.datetime.now() - datetime.timedelta(days=2), status.id))
        status.invalidate_recordset()
        request = self._run({'status': 'SUCCESS'})
        request.assert_not_called()
        self.assertEqual(status.state, 'timeout')
        self.assertIn('within 1 day', status.error_message)

    def test_only_the_limit_is_asked_in_one_run(self):
        statuses = [self._status('cron-5%s' % index) for index in range(3)]
        request = self._run({'status': 'SUCCESS', 'entityId': 'E'}, limit=2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual([status.state for status in statuses], ['success', 'success', 'pending'])

    def test_a_finished_status_is_not_asked_again(self):
        status = self._status('cron-6')
        status.state = 'success'
        self._run({'status': 'FAILURE'}).assert_not_called()
        self.assertEqual(status.state, 'success')

    def test_an_instance_that_is_not_confirmed_is_left_alone(self):
        status = self._status('cron-7')
        self.mk_instance.state = 'draft'
        try:
            self._run({'status': 'SUCCESS'}).assert_not_called()
        finally:
            self.mk_instance.state = 'confirmed'
        self.assertEqual(status.state, 'pending')

    def test_an_invoice_upload_status_is_left_to_the_invoice_export(self):
        status = self._status('cron-8')
        self.env['bol.invoice.request'].create({'order_id': 'INV-ORDER', 'shipment_id': 'INV-SHIP', 'mk_instance_id': self.mk_instance.id, 'bol_process_status_id': status.id})
        self._run({'status': 'SUCCESS'}).assert_not_called()
        self.assertEqual(status.state, 'pending')

    def test_a_finished_catalog_job_lets_the_waiting_offer_go_on(self):
        status = self._status('cron-9')
        product = self.env['product.product'].create({'name': 'Catalog Product', 'type': 'consu'})
        listing = self.env['mk.listing'].create({'name': product.name, 'mk_instance_id': self.mk_instance.id, 'product_tmpl_id': product.product_tmpl_id.id,
                                                 'bol_content_process_id': status.id})
        with patch.object(type(listing), '_bol_check_catalog_job') as check:
            self._run({'status': 'SUCCESS', 'entityId': 'E9'})
        check.assert_called_once()

    def test_a_status_that_is_still_pending_does_not_trigger_the_catalog_check(self):
        status = self._status('cron-10')
        product = self.env['product.product'].create({'name': 'Catalog Product 2', 'type': 'consu'})
        self.env['mk.listing'].create({'name': product.name, 'mk_instance_id': self.mk_instance.id, 'product_tmpl_id': product.product_tmpl_id.id,
                                       'bol_content_process_id': status.id})
        with patch.object(type(self.env['mk.listing']), '_bol_check_catalog_job') as check:
            self._run({'status': 'PENDING'})
        check.assert_not_called()
        self.assertEqual(status.state, 'pending')
