import datetime
import json
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase


def fake_bol_order(order_id, items):
    """Static answer of GET retailer/orders/{id} (v10.22+): unitPrice is the price before the discounts, totalPrice the result."""
    return {
        'orderId': order_id, 'pickupPoint': False, 'orderPlacedDateTime': '2026-11-01T20:07:00+01:00',
        'shipmentDetails': {'firstName': 'Baki', 'surname': 'Halici', 'streetName': 'Matthijs Marisstraat', 'houseNumber': '29',
                            'zipCode': '3314TN', 'city': 'Dordrecht', 'countryCode': 'NL', 'email': 'baki@example.com'},
        'orderItems': items,
    }


def fake_bol_item(item_id, offer_id, quantity, unit_price, discounts=None, title='Siveno Baby Laundry Soap'):
    item = {
        'orderItemId': item_id, 'cancellationRequest': False, 'fulfilment': {'method': 'FBR'},
        'offer': {'offerId': offer_id, 'reference': 'OE-T8LL-8TNC'}, 'product': {'ean': '8720000000001', 'title': title},
        'quantity': quantity, 'quantityShipped': 0, 'quantityCancelled': 0, 'unitPrice': unit_price,
        'totalPrice': round(unit_price * quantity - sum(d['amount'] for d in discounts or []), 2),
        'commission': 1.5, 'latestChangedDateTime': '2026-11-01T20:07:00+01:00',
    }
    if discounts is not None:
        item['discounts'] = discounts
    return item


class TestBolItemDiscount(TransactionCase):
    """Bol "X+Y for free" promotions. No credentials and no API call: the get-order answer is a static dict."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mk_instance = cls.env['mk.instance'].create({
            'name': 'Bol Test Instance', 'marketplace': 'bol', 'bol_client_id': 'test-client-id', 'bol_client_secret': 'test-client-secret',
            'bol_access_token': 'preset-test-token', 'bol_expires_in': '99999999',
        })
        cls.mk_instance.bol_last_token_generated_on = datetime.datetime.now()
        cls.mk_instance.set_pricelist('EUR')
        workflow = cls.env['order.workflow.config.ts'].create({'name': 'Bol Test Workflow'})
        cls.mk_instance.write({'bol_fbr_workflow_id': workflow.id, 'bol_fbb_workflow_id': workflow.id})
        # Dutch 21% VAT, price included: Bol always sends VAT included prices.
        cls.tax = cls.env['account.tax'].create({
            'name': 'Bol Test VAT 21%', 'amount': 21.0, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'price_include_override': 'tax_included', 'company_id': cls.mk_instance.company_id.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Siveno Baby Laundry Soap', 'type': 'consu', 'barcode': '8720000000001', 'default_code': 'OE-T8LL-8TNC',
            'list_price': 16.99, 'taxes_id': [(6, 0, cls.tax.ids)],
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Baki Halici'})

    def _import_lines(self, order_dict):
        """Create the order and its lines the way the import does, from the get-order answer."""
        order = self.env['sale.order'].create_bol_sale_order(order_dict, self.mk_instance, self.partner, self.partner)
        for item in order_dict['orderItems']:
            self.env['mk.listing.item'].create({
                'name': self.product.name, 'product_id': self.product.id, 'mk_instance_id': self.mk_instance.id,
                'mk_id': item['offer']['offerId'], 'is_listed': True,
                'mk_listing_id': self.env['mk.listing'].create({
                    'name': self.product.name, 'mk_instance_id': self.mk_instance.id, 'product_tmpl_id': self.product.product_tmpl_id.id,
                    'mk_id': item['offer']['offerId'], 'is_listed': True}).id,
            })
        self.assertTrue(self.env['sale.order'].create_sale_order_line_bol(self.mk_instance, order_dict, order))
        return order

    def test_buy_2_get_1_free_matches_bol_total(self):
        """Order C000K14821: 3 x 16.99, "Buy 2, Get 1 Free" -> 16.99 off, Bol total 33.98."""
        item = fake_bol_item('9000000000100', 'offer-xy-1', 3, 16.99, [{'title': 'Buy 2, Get 1 Free', 'amount': 16.99}])
        order = self._import_lines(fake_bol_order('C000K14821', [item]))
        line = order.order_line
        self.assertEqual(line.price_unit, 16.99)
        self.assertEqual(line.product_uom_qty, 3)
        self.assertAlmostEqual(line.discount, 33.33, delta=0.01)
        self.assertEqual(line.marketplace_discount_amount, 16.99)
        self.assertAlmostEqual(order.amount_total, 33.98, delta=0.02)
        # VAT is taken on the discounted price: 33.98 / 1.21 = 28.08, tax 5.90.
        self.assertAlmostEqual(order.amount_untaxed, 28.08, delta=0.02)
        self.assertAlmostEqual(order.amount_tax, 5.90, delta=0.02)

    def test_real_bol_response_c000k14821(self):
        """Real get-order answer of Bol for C000K14821 (2+1 promotion), customer data anonymised, prices and discount untouched."""
        bol_order = {
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
                'product': {'ean': '8681511099374', 'title': "Siveno's 100% Natuurlijke Wasmiddel voor Babykleding – Beste Keuze voor Hypoallergene Verzorging en Veilige Waspraktijken"},
                'quantity': 3, 'quantityCancelled': 0, 'quantityShipped': 0, 'totalPrice': 33.98, 'unitPrice': 16.99}],
            'orderPlacedDateTime': '2026-11-01T20:07:16+01:00', 'pickupPoint': False,
            'shipmentDetails': {'city': 'Plaats', 'countryCode': 'NL', 'email': 'anon@verkopen.bol.com', 'firstName': 'Test', 'houseNumber': '1',
                                'language': 'nl', 'salutation': 'MALE', 'streetName': 'Teststraat', 'surname': 'Klant', 'zipCode': '1000AA'},
        }
        order = self._import_lines(bol_order)
        line = order.order_line
        self.assertEqual((line.price_unit, line.product_uom_qty), (16.99, 3))
        self.assertAlmostEqual(line.discount, 33.33, delta=0.01)
        self.assertEqual(line.marketplace_discount_amount, 16.99)
        self.assertEqual(line.bol_commission, 3.89)
        self.assertAlmostEqual(order.amount_total, bol_order['orderItems'][0]['totalPrice'], delta=0.01)

    def test_order_without_discounts_key_is_unchanged(self):
        item = fake_bol_item('9000000000101', 'offer-xy-2', 3, 16.99)
        self.assertNotIn('discounts', item)
        order = self._import_lines(fake_bol_order('C000K14822', [item]))
        self.assertEqual(order.order_line.discount, 0.0)
        self.assertEqual(order.order_line.marketplace_discount_amount, 0.0)
        self.assertAlmostEqual(order.amount_total, 50.97, delta=0.02)

    def test_empty_discounts_list_is_no_discount(self):
        item = fake_bol_item('9000000000102', 'offer-xy-3', 3, 16.99, [])
        order = self._import_lines(fake_bol_order('C000K14823', [item]))
        self.assertEqual(order.order_line.discount, 0.0)
        self.assertAlmostEqual(order.amount_total, 50.97, delta=0.02)

    def test_several_discounts_are_added_up(self):
        item = fake_bol_item('9000000000103', 'offer-xy-4', 1, 10.0, [{'title': 'a', 'amount': 1.0}, {'title': 'b', 'amount': 1.5}])
        order = self._import_lines(fake_bol_order('C000K14824', [item]))
        self.assertAlmostEqual(order.order_line.discount, 25.0, delta=0.01)
        self.assertEqual(order.order_line.marketplace_discount_amount, 2.5)
        self.assertAlmostEqual(order.amount_total, 7.5, delta=0.02)

    def test_discount_percent_never_goes_over_100(self):
        item = fake_bol_item('9000000000104', 'offer-xy-5', 1, 5.0, [{'title': 'too much', 'amount': 9.0}])
        order = self._import_lines(fake_bol_order('C000K14825', [item]))
        self.assertEqual(order.order_line.discount, 100.0)

    def test_negative_discount_amount_is_ignored(self):
        """A negative amount is not a valid Bol discount: percent and amount stay 0 and the price is never raised."""
        item = fake_bol_item('9000000000107', 'offer-xy-8', 2, 10.0, [{'title': 'wrong sign', 'amount': -2.0}])
        order = self._import_lines(fake_bol_order('C000K14828', [item]))
        line = order.order_line
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.marketplace_discount_amount, 0.0)
        self.assertEqual(line.price_unit, 10.0)
        self.assertAlmostEqual(order.amount_total, 20.0, delta=0.02)

    def test_excluded_tax_setting_keeps_the_same_total(self):
        """With "managing excluded taxes on Odoo product" the price loses its VAT, the percent still applies."""
        self.mk_instance.bol_managing_excluded_taxes_on_product = True
        self.tax.price_include_override = 'tax_excluded'
        item = fake_bol_item('9000000000105', 'offer-xy-6', 3, 16.99, [{'title': 'Buy 2, Get 1 Free', 'amount': 16.99}])
        order = self._import_lines(fake_bol_order('C000K14826', [item]))
        self.assertAlmostEqual(order.order_line.discount, 33.33, delta=0.01)
        self.assertAlmostEqual(order.amount_total, 33.98, delta=0.02)

    def test_import_from_api_answer(self):
        """The whole fetch: the get-order answer goes through the mocked HTTP call, nothing else is faked."""
        item = fake_bol_item('9000000000106', 'offer-xy-7', 3, 16.99, [{'title': 'Buy 2, Get 1 Free', 'amount': 16.99}])
        response = MagicMock(status_code=200, reason='OK', text=json.dumps(fake_bol_order('C000K14827', [item])))
        response.raise_for_status.return_value = None
        with patch('requests.request', return_value=response):
            order_dict = self.env['sale.order'].fetch_orders_from_bol(self.mk_instance, mk_order_id='C000K14827')
        order = self._import_lines(order_dict)
        self.assertAlmostEqual(order.amount_total, 33.98, delta=0.02)
