import base64
import datetime
import json
from unittest.mock import MagicMock, patch

import requests

from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.tests.common import TransactionCase

from .test_backport import fake_response

# Real get-order answer of Bol for C000K14821 (2+1 promotion), customer data anonymised, prices and discount untouched.
REAL_BOL_ORDER = {
    'billingDetails': {'city': 'Plaats', 'countryCode': 'NL', 'email': 'anon@verkopen.bol.com', 'firstName': 'Test', 'houseNumber': '1',
                       'orderReference': 'C000K14821', 'salutation': 'MALE', 'streetName': 'Teststraat', 'surname': 'Klant', 'zipCode': '1000AA'},
    'orderId': 'C000K14821',
    'orderItems': [{
        'cancellationRequest': False, 'commission': 3.89,
        'discounts': [{'amount': 16.99, 'title': '2+1 gratis op Siveno & Hoito'}],
        'fulfilment': {'distributionParty': 'RETAILER', 'expiryDate': '2026-11-08', 'latestDeliveryDate': '2026-11-05', 'method': 'FBR', 'timeFrameType': 'REGULAR'},
        'latestChangedDateTime': '2026-11-01T20:08:16+01:00',
        'offer': {'offerId': '7dddad90-3a17-4534-b6bb-ac509016449e', 'reference': 'OE-T8LL-8TNC'},
        'orderItemId': '4016990144',
        'product': {'ean': '8681511099374', 'title': "Siveno's 100% Natuurlijke Wasmiddel voor Babykleding"},
        'quantity': 3, 'quantityCancelled': 0, 'quantityShipped': 0, 'totalPrice': 33.98, 'unitPrice': 16.99}],
    'orderPlacedDateTime': '2026-11-01T20:07:16+01:00', 'pickupPoint': False,
    'shipmentDetails': {'city': 'Plaats', 'countryCode': 'NL', 'email': 'anon@verkopen.bol.com', 'firstName': 'Test', 'houseNumber': '1',
                        'language': 'nl', 'salutation': 'MALE', 'streetName': 'Teststraat', 'surname': 'Klant', 'zipCode': '1000AA'},
}
REAL_BOL_ORDER_LIST = {'orders': [{'orderId': 'C000K14821', 'orderPlacedDateTime': '2026-11-01T20:07:16+01:00', 'orderItems': [
    {'orderItemId': '4016990144', 'ean': '8681511099374', 'fulfilmentMethod': 'FBR', 'fulfilmentStatus': 'OPEN',
     'quantity': 3, 'quantityShipped': 0, 'quantityCancelled': 0}]}]}


class TestTokenRequest(TransactionCase):
    """Items 2 and 4: the token request is the one of the Bol.com documentation, and nothing is faked any more."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mk_instance = cls.env['mk.instance'].create({
            'name': 'Bol Token Instance', 'marketplace': 'bol', 'bol_client_id': 'my-client', 'bol_client_secret': 'my-secret'})

    def test_token_request_follows_the_documentation(self):
        answer = fake_response({'access_token': 'real-token-123', 'token_type': 'Bearer', 'expires_in': 299, 'scope': 'RETAILER'})
        with patch('requests.request', return_value=answer) as request:
            self.mk_instance.with_context(token_expired=True).bol_generate_new_token()
        self.assertEqual(request.call_count, 1)
        args, kwargs = request.call_args
        self.assertEqual(args, ('POST', 'https://login.bol.com/token'))
        self.assertEqual(kwargs['data'], {'grant_type': 'client_credentials'})
        self.assertEqual(kwargs['headers']['Authorization'], 'Basic %s' % base64.b64encode(b'my-client:my-secret').decode())
        self.assertEqual(kwargs['headers']['Accept'], 'application/json')
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/x-www-form-urlencoded')

    def test_token_and_expiry_are_stored(self):
        answer = fake_response({'access_token': 'real-token-123', 'token_type': 'Bearer', 'expires_in': 299, 'scope': 'RETAILER'})
        with patch('requests.request', return_value=answer):
            self.mk_instance.with_context(token_expired=True).bol_generate_new_token()
        self.assertEqual(self.mk_instance.bol_access_token, 'real-token-123')
        self.assertEqual(float(self.mk_instance.bol_expires_in), 299)
        self.assertTrue(self.mk_instance.validate_bol_token_hash())

    def test_valid_token_is_reused_without_a_request(self):
        self.mk_instance.write({'bol_access_token': 'still-good', 'bol_expires_in': '299', 'bol_last_token_generated_on': datetime.datetime.now()})
        with patch('requests.request') as request:
            self.mk_instance.bol_generate_new_token()
        request.assert_not_called()
        self.assertEqual(self.mk_instance.bol_access_token, 'still-good')

    def test_refused_credentials_raise_and_store_nothing(self):
        answer = fake_response({'error': 'invalid_client'}, 401)
        answer.content = b'{"error": "invalid_client"}'
        with patch('requests.request', return_value=answer):
            with self.assertRaises(MarketplaceException):
                self.mk_instance.with_context(token_expired=True).bol_generate_new_token()
        self.assertFalse(self.mk_instance.bol_access_token)


class TestImportThroughTheApi(TransactionCase):
    """The whole order import with only the HTTP call faked: token, order list, order, queue, sale order. No static answers in the module."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mk_instance = cls.env['mk.instance'].create({
            'name': 'Bol Smoke Instance', 'marketplace': 'bol', 'bol_client_id': 'smoke-client', 'bol_client_secret': 'smoke-secret'})
        cls.mk_instance.set_pricelist('EUR')
        workflow = cls.env['order.workflow.config.ts'].create({'name': 'Bol Smoke Workflow'})
        cls.mk_instance.write({'bol_fbr_workflow_id': workflow.id, 'bol_fbb_workflow_id': workflow.id})
        tax = cls.env['account.tax'].create({
            'name': 'Smoke VAT 21%', 'amount': 21.0, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'price_include_override': 'tax_included', 'company_id': cls.mk_instance.company_id.id})
        product = cls.env['product.product'].create({
            'name': 'Siveno Baby Laundry Soap', 'type': 'consu', 'barcode': '8681511099374', 'default_code': 'OE-T8LL-8TNC',
            'list_price': 16.99, 'taxes_id': [(6, 0, tax.ids)]})
        listing = cls.env['mk.listing'].create({
            'name': product.name, 'mk_instance_id': cls.mk_instance.id, 'product_tmpl_id': product.product_tmpl_id.id,
            'mk_id': '7dddad90-3a17-4534-b6bb-ac509016449e', 'is_listed': True})
        cls.env['mk.listing.item'].create({
            'name': product.name, 'product_id': product.id, 'mk_listing_id': listing.id, 'mk_id': '7dddad90-3a17-4534-b6bb-ac509016449e',
            'mk_instance_id': cls.mk_instance.id, 'is_listed': True})

    def _bol(self, requests_made):
        """What Bol.com answers, by address. Anything else is a failure of the test: the module may only ask for these."""
        def answer(method, url, **kwargs):
            requests_made.append((method, url, dict(kwargs.get('params') or {})))
            if url == 'https://login.bol.com/token':
                return fake_response({'access_token': 'smoke-token', 'token_type': 'Bearer', 'expires_in': 299, 'scope': 'RETAILER'})
            if url == 'https://api.bol.com/retailer/orders':
                return fake_response(REAL_BOL_ORDER_LIST if kwargs['params'].get('page') == 1 else {})
            if url == 'https://api.bol.com/retailer/orders/C000K14821':
                return fake_response(REAL_BOL_ORDER)
            raise AssertionError('Unexpected request to Bol.com: %s %s' % (method, url))
        return answer

    def test_order_list_comes_from_the_api(self):
        made = []
        with patch('requests.request', self._bol(made)):
            orders = self.env['sale.order'].fetch_orders_from_bol(self.mk_instance)
        self.assertEqual([order['orderId'] for order in orders], ['C000K14821'])
        self.assertEqual([url for dummy, url, dummy2 in made if 'login' not in url], ['https://api.bol.com/retailer/orders'] * 2)
        self.assertEqual([params['page'] for dummy, url, params in made if url.endswith('retailer/orders')], [1, 2])

    def test_single_order_comes_from_the_api(self):
        with patch('requests.request', self._bol([])):
            order = self.env['sale.order'].fetch_orders_from_bol(self.mk_instance, mk_order_id='C000K14821')
        self.assertEqual(order['orderItems'][0]['discounts'], [{'amount': 16.99, 'title': '2+1 gratis op Siveno & Hoito'}])
        self.assertEqual(order['orderItems'][0]['totalPrice'], 33.98)

    def test_an_unknown_order_is_an_api_error_not_a_static_answer(self):
        def answer(method, url, **kwargs):
            if 'login.bol.com' in url:
                return fake_response({'access_token': 'smoke-token', 'expires_in': 299})
            return fake_response({'detail': 'Order not found'}, 404)
        with patch('requests.request', answer):
            with self.assertRaises(MarketplaceException):
                self.env['sale.order'].fetch_orders_from_bol(self.mk_instance, mk_order_id='C000K99999')

    def test_import_queue_and_sale_order_with_the_discount(self):
        made = []
        with patch('requests.request', self._bol(made)), patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'):
            self.env['sale.order'].bol_import_orders(self.mk_instance)
            queue = self.env['mk.queue.job'].search([('mk_instance_id', '=', self.mk_instance.id)], order='id desc', limit=1)
            self.assertEqual(queue.mk_queue_line_ids.mapped('mk_id'), ['C000K14821'])
            queue.do_process()
        self.assertEqual(queue.mk_queue_line_ids.state, 'processed')
        order = self.env['sale.order'].search([('mk_id', '=', 'C000K14821'), ('mk_instance_id', '=', self.mk_instance.id)])
        self.assertEqual(len(order), 1)
        line = order.order_line
        self.assertEqual((line.price_unit, line.product_uom_qty), (16.99, 3))
        self.assertAlmostEqual(line.discount, 33.33, delta=0.01)
        self.assertEqual(line.marketplace_discount_amount, 16.99)
        self.assertEqual(line.bol_commission, 3.89)
        self.assertAlmostEqual(order.amount_untaxed, 28.08, delta=0.02)
        self.assertAlmostEqual(order.amount_tax, 5.90, delta=0.02)
        self.assertAlmostEqual(order.amount_total, 33.98, delta=0.02)
        # the token was asked for once, with the credentials of the instance, and every later call used it
        self.assertEqual(sum(1 for dummy, url, dummy2 in made if url == 'https://login.bol.com/token'), 1)

    def test_an_order_that_is_already_imported_is_not_imported_twice(self):
        with patch('requests.request', self._bol([])), patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'):
            self.env['sale.order'].bol_import_orders(self.mk_instance)
            self.env['mk.queue.job'].search([('mk_instance_id', '=', self.mk_instance.id)], order='id desc', limit=1).do_process()
            before = self.env['mk.queue.job'].search_count([('mk_instance_id', '=', self.mk_instance.id)])
            self.env['sale.order'].bol_import_orders(self.mk_instance)
        self.assertEqual(self.env['mk.queue.job'].search_count([('mk_instance_id', '=', self.mk_instance.id)]), before)
        self.assertEqual(self.env['sale.order'].search_count([('mk_id', '=', 'C000K14821'), ('mk_instance_id', '=', self.mk_instance.id)]), 1)
