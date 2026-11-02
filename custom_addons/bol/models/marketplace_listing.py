import logging
import pprint
from datetime import datetime

from odoo import models, fields, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DF, html_escape
from odoo.tools.misc import split_every

from .marketplace_instance import BOL_ACCEPT_V10, BOL_CONTENT_LANGUAGE_BY_COUNTRY, BOL_OFFERS_ACCEPT_V11, BOL_CONDITION_CATEGORY, BOL_CONDITION_STATE, BOL_CONDITION_GRADE, BOL_FULFILMENT_SCHEDULE, \
    BOL_ULTIMATE_ORDER_TIME
from .misc import _normalize_barcode_for_bol, _get_possible_barcodes

_logger = logging.getLogger("TeqStars:bol")

BOL_CONDITION = [('NEW', 'New'), ('AS_NEW', 'As New'), ('GOOD', 'Good'), ('REASONABLE', 'Reasonable'), ('MODERATE', 'Moderate')]

# https://developers.bol.com/appendix-c-delivery-codes/
BOL_DELIVERY_CODES = [
    ('24uurs-23', 'Ordered before 23:00 on working days, delivered the next working day.'),
    ('24uurs-22', 'Ordered before 22:00 on working days, delivered the next working day.'),
    ('24uurs-21', 'Ordered before 21:00 on working days, delivered the next working day.'),
    ('24uurs-20', 'Ordered before 23:00 on working days, delivered the next working day.'),
    ('24uurs-19', 'Ordered before 19:00 on working days, delivered the next working day.'),
    ('24uurs-18', 'Ordered before 18:00 on working days, delivered the next working day.'),
    ('24uurs-17', 'Ordered before 17:00 on working days, delivered the next working day.'),
    ('24uurs-16', 'Ordered before 16:00 on working days, delivered the next working day.'),
    ('24uurs-15', 'Ordered before 15:00 on working days, delivered the next working day.'),
    ('24uurs-14', 'Ordered before 14:00 on working days, delivered the next working day.'),
    ('24uurs-13', 'Ordered before 13:00 on working days, delivered the next working day.'),
    ('24uurs-12', 'Ordered before 12:00 on working days, delivered the next working day.'),
    ('1-2d', '1-2 working days.'),
    ('2-3d', '2-3 working days.'),
    ('3-5d', '3-5 working days.'),
    ('4-8d', '4-8 working days.'),
    ('1-8d', '1-8 working days.'),
    ('VVB', 'VVB'),
    ('MijnLeverbelofte', 'Your own specific deliveryschedule as configured in your bol.com account')  # only available if you are part of the pilot-group.
]
BOL_FULFILMENT_METHOD = [('FBB', 'Fulfilment by bol.com'), ('FBR', 'Fulfilment by retailer')]
# A weak guess would file the product under the wrong category in the catalog every seller shares, so a suggestion is only used above this probability.
BOL_GPC_MIN_PROBABILITY = 0.5


class MkListing(models.Model):
    _inherit = "mk.listing"

    bol_condition = fields.Selection(BOL_CONDITION, string='Condition', default="NEW",
                                     help='Legacy v10 condition selection. Kept read-only for historical reads; v11 payloads use bol_condition_category and sub-fields.')
    bol_delivery_code = fields.Selection(BOL_DELIVERY_CODES, string='Promised delivery time',
                                         help='Legacy v10 delivery code. Kept read-only for historical reads; v11 payloads use bol_fulfilment_schedule and deliveryPromise sub-fields.')
    bol_fulfilment_method = fields.Selection(BOL_FULFILMENT_METHOD, string='Fulfilment Method', default='FBR',
                                             help='Specifies whether this shipment has been fulfilled by the retailer (FBR) or fulfilled by bol.com (FBB). Defaults to FBR.')
    bol_not_publishable_reasons = fields.Text('Not Publishable Reasons', help="Error message describing the reason the offer is invalid.")
    bol_onhold_by_retailer = fields.Boolean('On Hold', help="Indicates whether or not you want to put this offer for sale on the bol.com website. Defaults to false.")
    bol_skip_listing_sync = fields.Boolean('Skip Listing Sync', default=False, copy=False,
                                           help="When enabled, the 'Sync to Odoo' and 'Sync to Bol.com' buttons are not available for this listing.")

    bol_condition_category = fields.Selection(BOL_CONDITION_CATEGORY, string='Condition Category', default='NEW',
                                              help='Offers API v11 condition category. Drives which sub-fields apply.')
    bol_condition_state = fields.Selection(BOL_CONDITION_STATE, string='Condition State',
                                           help='Required when condition category is SECONDHAND.')
    bol_condition_comment = fields.Text('Condition Comment', help='Optional free-text addition to the SECONDHAND state. Max 2000 characters. E-mail addresses are not allowed.')
    bol_condition_grade = fields.Selection(BOL_CONDITION_GRADE, string='Refurbished Grade', help='Required when condition category is REFURBISHED.')
    bol_condition_margin = fields.Boolean('Margin Adjustment', help='Refurbished only. If true, VAT was paid earlier (0% tax rate).')

    bol_fulfilment_schedule = fields.Selection(BOL_FULFILMENT_SCHEDULE, string='Fulfilment Schedule',
                                               help='Offers API v11 fulfilment schedule. Required when Fulfilment Method is FBR.')
    bol_delivery_min_days = fields.Integer('Min Days To Customer', help='v11 deliveryPromise.minimumDaysToCustomer. Required when schedule is BOL_DELIVERY_PROMISE.')
    bol_delivery_max_days = fields.Integer('Max Days To Customer', help='v11 deliveryPromise.maximumDaysToCustomer. Required when schedule is BOL_DELIVERY_PROMISE.')
    bol_ultimate_order_time = fields.Selection(BOL_ULTIMATE_ORDER_TIME, string='Ultimate Order Time',
                                               help='v11 deliveryPromise.ultimateOrderTime. Required when schedule is BOL_DELIVERY_PROMISE with min=0 and max=1.')

    bol_content_process_id = fields.Many2one('bol.process.status', string='Catalog Job', readonly=True, copy=False, ondelete='set null',
                                             help='Background job at Bol.com that builds the catalog product for this barcode. It is cleared once Bol.com reports the result in the log.')

    def _sync_by_barcode(self, mk_instance_id, variant_barcode, variant_id, listing_item_id):
        if mk_instance_id.marketplace != 'bol':
            return super()._sync_by_barcode(mk_instance_id, variant_barcode, variant_id, listing_item_id)
        return self._bol_sync_by_barcode_with_fallback(mk_instance_id, variant_barcode, variant_id, listing_item_id)

    def _bol_sync_by_barcode_with_fallback(self, mk_instance_id, variant_barcode, variant_id, listing_item_id):
        listing_item_obj = self.env['mk.listing.item']
        if not variant_barcode or listing_item_id:
            return listing_item_id, False
        odoo_product_obj = self.env['product.product']
        for candidate in _get_possible_barcodes(variant_barcode):
            item = listing_item_obj.search([('product_id.barcode', '=', candidate), '|', ('mk_id', '=', variant_id), ('mk_id', '=', False), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            if item:
                return item, False
            product = odoo_product_obj.search([('barcode', '=', candidate)], limit=1)
            if product:
                return listing_item_obj, product
        return listing_item_obj, False

    def bol_hide_fields(self):
        return ['product_category_id', 'listing_create_date', 'listing_update_date', 'listing_publish_date']

    def bol_hide_page(self):
        return ['description', 'product_images']

    def put_listing_on_hold(self):
        self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(self.mk_id), {'onHoldByRetailer': True}, method="PATCH", accept=BOL_OFFERS_ACCEPT_V11)
        self.write({'is_published': False, 'bol_onhold_by_retailer': True})
        self._bol_fetch_not_for_sale_reasons()
        return True

    def put_listing_on_unhold(self):
        self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(self.mk_id), {'onHoldByRetailer': False}, method="PATCH", accept=BOL_OFFERS_ACCEPT_V11)
        self.write({'is_published': True, 'bol_onhold_by_retailer': False})
        self._bol_fetch_not_for_sale_reasons()
        return True

    def bol_published(self):
        if not self.is_published:
            self.put_listing_on_unhold()
        else:
            self.put_listing_on_hold()
        return True

    def bol_toggle_listing_sync(self):
        for listing in self:
            listing.bol_skip_listing_sync = not listing.bol_skip_listing_sync
        return True

    def _bol_check_listing_sync_allowed(self):
        self.ensure_one()
        if self.bol_skip_listing_sync:
            raise MarketplaceException(_("Sync is not allowed for listing '%s'. Click 'Allow Sync' on the listing first.") % self.name)
        if self.mk_instance_id.state != 'confirmed':
            raise MarketplaceException(_("You can sync a listing only with a confirmed instance. Please confirm the instance and try again."))

    def bol_sync_listing_to_odoo(self):
        """
        This method is used to update the listing by importing the latest data from Bol.com.
        """
        self.ensure_one()
        self._bol_check_listing_sync_allowed()
        mk_instance_id = self.mk_instance_id
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
        mk_log_line_dict = {'error': [], 'success': []}
        try:
            # Fetch the offer from Bol.com
            offer_response = mk_instance_id._send_bol_request('retailer/offers/{}'.format(self.mk_id), {}, method='GET', accept=BOL_OFFERS_ACCEPT_V11)
            self.with_context(mk_log_line_dict=mk_log_line_dict, mk_log_id=mk_log_id).create_update_bol_product(
                offer_response, mk_instance_id, update_product_price=True, is_update_existing_products=True)

            if mk_log_id and not mk_log_id.log_line_ids:
                mk_log_id.unlink()
                message = _("The listing has been updated with the latest data from Bol.com.")
            else:
                message = (
                    _('The listing has been updated with the latest data from Bol.com.'
                      '<br/>Please check the log <b><a href="/web#id=%(log_id)s&model=mk.log&view_type=form">'
                      '%(log_name)s</a></b> for details regarding the update.')
                    % {'log_id': mk_log_id.id, 'log_name': html_escape(mk_log_id.name)}
                )

            self.env.user._bus_send(
                'marketplace_notification',
                {
                    'title': _('Update Completed'),
                    'message': message,
                    'message_is_html': True,
                    'type': 'info',
                    'sticky': False,
                }
            )
        except Exception as e:
            raise MarketplaceException("Unable to update the listing with the latest data from Bol.com. Error: %s" % str(e))

    def _bol_prepare_offer_patch_body(self):
        """Fulfilment, stock (FBR) and price of the offer, for one PATCH. Bol.com accepts or refuses the whole request."""
        self.ensure_one()
        patch_body = {'fulfilment': self._bol_prepare_fulfilment_payload()}
        for listing_item in self.listing_item_ids.filtered(lambda x: x.mk_listing_id.bol_fulfilment_method == 'FBR' and x.product_id.is_storable):
            quantity = listing_item.product_id.get_product_stock(listing_item.export_qty_type, listing_item.export_qty_value,
                                                                 self.mk_instance_id.warehouse_id.lot_stock_id, self.mk_instance_id.stock_field_id.sudo().name)
            patch_body['stock'] = {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True}
        for listing_item in self.listing_item_ids:
            price_dict = listing_item.get_offer_price_for_bol()
            if price_dict and price_dict.get('bundlePrices'):
                patch_body['pricing'] = price_dict
        return patch_body

    def bol_sync_listing_to_bol(self):
        """
        One-click direct update from Odoo to Bol.com without opening the wizard. Updates the offer details, price and quantity in one request.
        """
        self.ensure_one()
        self._bol_check_listing_sync_allowed()
        self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(self.mk_id), self._bol_prepare_offer_patch_body(), method='PATCH', accept=BOL_OFFERS_ACCEPT_V11)
        self._bol_fetch_not_for_sale_reasons()
        self.env.user._bus_send(
            'marketplace_notification',
            {
                'title': _('Sync Completed'),
                'message': _("The listing has been synced to Bol.com with the latest data from Odoo."),
                'message_is_html': False,
                'type': 'info',
                'sticky': False,
            }
        )
        return True

    def create_odoo_template_for_bol_product(self, bol_offer_dict, existing_odoo_product, mk_instance_id, update_product_price):
        odoo_template_obj = self.env["product.template"]
        product_title = bol_offer_dict.get('offer_title') or bol_offer_dict.get('reference') or bol_offer_dict.get('ean')
        product_template_vals = {
            'name': product_title,
            'type': 'consu',
            'is_storable': True,
            'default_code': bol_offer_dict.get('reference'),
        }
        product_tmpl_id = odoo_template_obj.sudo().create(product_template_vals)
        product_tml_update_vals = {}
        if bol_offer_dict.get('ean'):
            ean = bol_offer_dict.get('ean')
            product_tml_update_vals.update({'barcode': _normalize_barcode_for_bol(ean) or ean})
        bundle_pricing = bol_offer_dict.get('pricing', {}).get('bundlePrices') or []
        price = 0.0
        for price_dict in bundle_pricing:
            quantity, bol_price = price_dict.get('quantity'), price_dict.get('unitPrice')
            if quantity == 1:
                price = bol_price
                break
        if price and update_product_price:
            if mk_instance_id.pricelist_id.currency_id.id == product_tmpl_id.company_id.currency_id.id:
                product_tml_update_vals.update({'list_price': float(price)})
            else:
                mk_instance_currency_id = mk_instance_id.pricelist_id.currency_id
                odoo_product_company_currency_id = product_tmpl_id.company_id.currency_id
                price_currency = mk_instance_currency_id._convert(float(price), odoo_product_company_currency_id, self.env.user.company_id, fields.Date.today())
                product_tml_update_vals.update({'list_price': price_currency})
        product_tmpl_id.write(product_tml_update_vals)
        product_tmpl_id = product_tmpl_id.sudo(False)
        existing_odoo_product.update({bol_offer_dict.get('offerId'): product_tmpl_id.product_variant_ids})
        return product_tmpl_id

    def _fetch_not_for_sale_reasons(self, mk_instance_id, mk_id):
        """Returns list of formatted lines for matching country only."""
        try:
            resp = mk_instance_id.with_context(skip_raise=True)._send_bol_request('retailer/offers/{}/not-for-sale-reasons'.format(mk_id), {}, method='GET', accept=BOL_OFFERS_ACCEPT_V11)
            if not resp or resp is True:
                return []
            instance_country_code = mk_instance_id.country_id.code
            lines = []
            for country in resp.get('countries', []):
                cc = country.get('countryCode', '')
                # filtered by the instance country code
                if cc != instance_country_code:
                    continue
                for reason in country.get('reasons', []):
                    code = reason.get('code', '')
                    desc = reason.get('description', '')
                    missing = ', '.join(a.get('name', '') for a in reason.get('missingAttributes', []) if a.get('name'))
                    line = "{} - [{}]: {}".format(cc, code, desc)
                    if missing:
                        line += " (missing: {})".format(missing)
                    lines.append(line)
            return lines
        except Exception as e:
            _logger.warning("Bol not-for-sale-reasons fetch failed for %s: %s", mk_id, e)
            return []

    def prepare_marketplace_listing_vals_for_bol(self, bol_offer_dict, mk_instance_id, odoo_product_id):
        mk_id = bol_offer_dict.get('offerId')
        offer_title = bol_offer_dict.get('offer_title') or bol_offer_dict.get('reference') or bol_offer_dict.get('ean')
        country_avails = bol_offer_dict.get('countryAvailabilities') or []
        is_offline = any(c.get('forSale') is False for c in country_avails) if country_avails else False
        vals = {
            'name': offer_title,
            'mk_instance_id': mk_instance_id.id,
            'product_tmpl_id': odoo_product_id.product_tmpl_id.id,
            'mk_id': mk_id,
            'listing_create_date': False,
            'listing_update_date': False,
            'listing_publish_date': False,
            'is_published': not is_offline,
            'is_listed': True,
            'bol_onhold_by_retailer': bol_offer_dict.get('onHoldByRetailer'),
        }
        if is_offline:
            reason_for_not_sale = self._fetch_not_for_sale_reasons(mk_instance_id, mk_id)
            vals.update({'bol_not_publishable_reasons': '\n'.join(reason_for_not_sale) if reason_for_not_sale else False})
        else:
            # An offer Bol.com sells again has no reasons left; the old ones would stay on the form otherwise.
            vals.update({'bol_not_publishable_reasons': False})
        vals.update(self._bol_parse_condition_response(bol_offer_dict.get('condition') or {}))
        vals.update(self._bol_parse_fulfilment_response(bol_offer_dict.get('fulfilment') or {}))
        return vals

    def prepare_marketplace_listing_item_vals_for_bol(self, bol_offer_dict, mk_instance_id, odoo_product_id, mk_listing_id):
        offer_title = bol_offer_dict.get('offer_title') or bol_offer_dict.get('reference') or bol_offer_dict.get('ean')
        vals = {
            'name': offer_title,
            'product_id': odoo_product_id.id,
            'default_code': bol_offer_dict.get('reference'),
            'barcode': bol_offer_dict.get('ean'),
            'mk_listing_id': mk_listing_id.id,
            'mk_id': bol_offer_dict.get('offerId'),
            'mk_instance_id': mk_instance_id.id,
            'item_create_date': False,
            'item_update_date': False,
            'is_listed': True,
        }
        return vals

    def create_update_bol_product(self, bol_offer_dict, mk_instance_id, update_product_price=False, is_update_existing_products=True):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        mk_listing_item_obj = self.env['mk.listing.item']
        mk_id = bol_offer_dict.get('offerId')
        variant_barcode = bol_offer_dict.get('ean')
        variant_sku = bol_offer_dict.get('reference')
        if not bol_offer_dict.get('offer_title') and variant_barcode:
            # Same lookup _bol_import_listings uses for the bulk-import title: when the
            # caller (e.g. sale order import) didn't already resolve a title, ask Bol's
            # catalog for the name it publishes for this EAN before falling back to SKU/EAN.
            catalog_state, catalog_title = self._bol_get_catalog_title(mk_instance_id, variant_barcode)
            if catalog_title:
                bol_offer_dict['offer_title'] = catalog_title
        offer_title = bol_offer_dict.get('offer_title') or variant_sku or variant_barcode
        mk_listing_id = self.search([('mk_instance_id', '=', mk_instance_id.id), ('mk_id', '=', mk_id)])
        if mk_listing_id and not is_update_existing_products:
            log_message = _("IMPORT LISTING : Skipped {} as it's already exist!".format(mk_listing_id.name))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return mk_listing_id
        variant_sequence = 1

        # Checking validation for marketplace product for duplicated SKU or Barcode before start importing.
        listing_item_validation_dict = {'name': offer_title, 'id': mk_id,
                                        'variants': [{'sku': variant_sku, 'barcode': variant_barcode, 'id': mk_id}]}
        validated, log_message = self.check_for_duplicate_sku_or_barcode_in_marketplace_product(mk_instance_id.sync_product_with, listing_item_validation_dict)
        if not validated:
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False

        existing_mk_product = {}
        existing_odoo_product = {}
        odoo_product_template = self.env['product.template']
        odoo_product_id, listing_item_id = self.get_odoo_product_variant_and_listing_item(mk_instance_id, mk_id, variant_barcode, variant_sku)
        if odoo_product_id:
            odoo_product_template |= odoo_product_id.product_tmpl_id
            existing_odoo_product.update({mk_id: odoo_product_id})
        elif listing_item_id and not odoo_product_id:
            existing_odoo_product.update({mk_id: listing_item_id.product_id})
        listing_item_id and existing_mk_product.update({mk_id: listing_item_id})

        if len(odoo_product_template) > 1:
            log_message = _("IMPORT LISTING: Found multiple Odoo Product ({}) For Bol Offer : {}.".format(','.join([x.name for x in odoo_product_template]), offer_title))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False

        validated, log_message = self.check_validation_for_import_product(listing_item_validation_dict, existing_odoo_product, existing_mk_product)
        if not validated:
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False

        listing_item_id = existing_mk_product.get(mk_id, False)
        odoo_product_id = existing_odoo_product.get(mk_id, False)
        if not listing_item_id:
            if not mk_listing_id:
                if not odoo_product_template and not mk_instance_id.is_create_products:
                    log_message = _("Odoo Product not found for Bol Offer : {} and SKU: {} and Barcode : {}".format(offer_title, variant_sku, variant_barcode))
                    self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    return False
                if not odoo_product_template:
                    odoo_product_template = self.create_odoo_template_for_bol_product(bol_offer_dict, existing_odoo_product, mk_instance_id, update_product_price)
                    odoo_product_id = existing_odoo_product.get(mk_id, False)
                listing_vals = self.prepare_marketplace_listing_vals_for_bol(bol_offer_dict, mk_instance_id, odoo_product_id)
                mk_listing_id = self.create(listing_vals)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'success': [
                                                         {'log_message': _('IMPORT LISTING: {} successfully created'.format(mk_listing_id.name)), 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            if not odoo_product_id:
                log_message = _("Odoo Product Variant not found for Bol Offer : {} and SKU: {} and Barcode : {}".format(offer_title, variant_sku, variant_barcode))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                return False
            mk_listing_item_vals = self.prepare_marketplace_listing_item_vals_for_bol(bol_offer_dict, mk_instance_id, odoo_product_id, mk_listing_id)
            mk_listing_item_vals.update({'sequence': variant_sequence})
            listing_item_id = mk_listing_item_obj.create(mk_listing_item_vals)
            variant_sequence += 1
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={
                'success': [{'log_message': _('IMPORT LISTING ITEM : {} ({}) successfully created'.format(mk_listing_id.name, listing_item_id.mk_id)), 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        else:
            listing_vals = self.prepare_marketplace_listing_vals_for_bol(bol_offer_dict, mk_instance_id, odoo_product_id)
            mk_listing_id.write(listing_vals)
            mk_listing_item_vals = self.prepare_marketplace_listing_item_vals_for_bol(bol_offer_dict, mk_instance_id, odoo_product_id or listing_item_id.product_id, mk_listing_id)
            listing_item_id.write(mk_listing_item_vals)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'success': [
                                                     {'log_message': _('IMPORT LISTING: {} successfully updated'.format(mk_listing_id.name)), 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        listing_item_id.create_or_update_pricelist_item_for_bol(bol_offer_dict.get('pricing'), update_product_price=update_product_price)
        return mk_listing_id

    def bol_import_listings(self, mk_instance_id, mk_listing_id=False, update_product_price=False, update_existing_product=False):
        if mk_listing_id:
            mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
            offer_response = mk_instance_id._send_bol_request('retailer/offers/{}'.format(mk_listing_id), {}, method='GET', accept=BOL_OFFERS_ACCEPT_V11)
            mk_listing_id = self.with_context(mk_log_line_dict=mk_log_line_dict, mk_log_id=mk_log_id).create_update_bol_product(offer_response, mk_instance_id, update_product_price=True)
            if mk_log_line_dict.get('error'):
                raise MarketplaceException(_("Found problem during Import listing, Details are below.\n{}".format(
                    ',\n'.join([error_dict.get('log_message') for error_dict in mk_log_line_dict.get('error')]))))
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
            if mk_listing_id:
                return mk_instance_id.action_open_model_view(mk_listing_id.ids, 'mk.listing', 'Bol.com Listing')
            return True
        return self._bol_import_listings(mk_instance_id, update_product_price=update_product_price, update_existing_product=update_existing_product)

    def _bol_import_listings(self, mk_instance_id, update_product_price=False, update_existing_product=False):
        """Paginated JSON import. Replaces v10 CSV export/poll/download."""
        listing_obj = self.env['mk.listing']
        existing_listings = listing_obj.search([('mk_instance_id', '=', mk_instance_id.id)])
        # bol.com allows only ten catalog calls a second, so an offer that is already imported keeps the name it has
        # and the catalog is only asked about offers this instance has never seen.
        existing_name_by_mk_id = {listing.mk_id: listing.name for listing in existing_listings if listing.mk_id}
        received_offer_ids = set()
        queue_ids = self.env['mk.queue.job']
        cursor = None
        while True:
            params = {'page-size': 100}
            if cursor:
                params['cursor'] = cursor
            response = mk_instance_id._send_bol_request('retailer/offers', {}, params=params, method='GET', accept=BOL_OFFERS_ACCEPT_V11)
            if not isinstance(response, dict):
                break
            # one queue per page, so every queue holds at most page-size offers
            queue_id = False
            for offer in response.get('offers', []):
                offer_id = offer.get('offerId')
                if not offer_id:
                    continue
                received_offer_ids.add(offer_id)
                if not queue_id:
                    queue_id = mk_instance_id.with_context(update_product_price=update_product_price, update_existing_product=update_existing_product).action_create_queue(type='product')
                    queue_ids |= queue_id
                ean = offer.get('ean')
                offer_name = existing_name_by_mk_id.get(offer_id)
                if not offer_name:
                    catalog_state, product_catalog = self._bol_get_catalog_product(mk_instance_id, ean)
                    # bol.com answers with nothing at all for a barcode it does not know, so the title is only read when it found one.
                    catalog_attributes = product_catalog.get('attributes', []) if catalog_state == 'found' else []
                    offer_name = next((attribute.get('values', [{}])[0].get('value') for attribute in catalog_attributes if attribute.get('id') == 'Title'), offer.get('reference') or ean)
                line_vals = {
                    'mk_id': offer_id,
                    'state': 'draft',
                    'name': offer_name,
                    'data_to_process': pprint.pformat(dict(offer)),
                    'mk_instance_id': mk_instance_id.id,
                }
                queue_id.action_create_queue_lines(line_vals)
            cursor = (response.get('page') or {}).get('nextCursor')
            if not cursor:
                break
        if received_offer_ids:
            listing_to_delete = existing_listings.filtered(lambda x: x.mk_id not in received_offer_ids)
            listing_to_delete.sudo().unlink()
        if queue_ids:
            return mk_instance_id.action_open_model_view(queue_ids.ids, 'mk.queue.job', 'Bol.com Listing Queue')
        return True

    def bol_export_listing_to_mk(self, operation_wizard):
        self.ensure_one()
        if not self.listing_item_ids:
            raise MarketplaceException(_("Offer '{}'     should have at least one variant for export".format(self.name)))
        if len(self.listing_item_ids) > 1:
            raise MarketplaceException(_("Offer '{}' shouldn't have multiple variants.".format(self.name)))
        if not self.listing_item_ids.barcode:
            raise MarketplaceException(_("Offer '{}' must have barcode defined in variant tab's product.".format(self.name)))
        request_vals = self._bol_prepare_offer_payload()
        bol_publish_in_store = operation_wizard.bol_publish_in_store
        # Catalog first: Bol.com sells an offer only once its barcode has a product in the catalog, so the catalog product is requested here.
        self._bol_prepare_catalog_for_export(request_vals.get('ean'))
        if bol_publish_in_store:
            request_vals['onHoldByRetailer'] = bol_publish_in_store == 'hold'
        response = self.mk_instance_id._send_bol_request('retailer/offers', request_vals, method='POST', accept=BOL_OFFERS_ACCEPT_V11)
        _logger.info("EXPORT PRODUCT: \nRequest Data: {} \nReceived Response: {}".format(request_vals, response))
        offer_id = isinstance(response, dict) and response.get('offerId')
        if not offer_id:
            raise MarketplaceException(_("Failed to Export Offer '{}' : {}").format(self.name, response))
        self.write({
            'is_listed': True,
            'is_published': bol_publish_in_store == 'publish',
            'bol_onhold_by_retailer': bol_publish_in_store == 'hold',
            'mk_id': offer_id,
        })
        self.listing_item_ids.write({'is_listed': True, 'mk_id': offer_id})
        _logger.info("EXPORT PRODUCT: v11 sync response stored offerId %s for listing %s.", offer_id, self.name)
        self._bol_fetch_not_for_sale_reasons()
        # No self.env.cr.commit() here: every other write-heavy method in this file (and the
        # queue-based export path that superseded this one, see bol_product_export_queue_process)
        # leaves the commit to the caller. A commit hardcoded here raises
        # "Pending savepoints not released, cannot commit!" whenever this method runs inside any
        # savepoint (tests, queue_job-style retry wrappers, ...).
        return True

    def bol_update_listing_to_mk(self, operation_wizard):
        for listing_id in self:
            if operation_wizard.is_update_product:
                patch_body = {'fulfilment': listing_id._bol_prepare_fulfilment_payload()}
                if operation_wizard.bol_publish_in_store:
                    patch_body['onHoldByRetailer'] = operation_wizard.bol_publish_in_store == 'hold'
                listing_id.mk_instance_id._send_bol_request('retailer/offers/{}'.format(listing_id.mk_id), patch_body,method='PATCH', accept=BOL_OFFERS_ACCEPT_V11)
            if operation_wizard.is_set_quantity:
                for listing_item in listing_id.listing_item_ids.filtered(lambda x: x.mk_listing_id.bol_fulfilment_method == 'FBR' and x.product_id.is_storable):
                    quantity = listing_item.product_id.get_product_stock(listing_item.export_qty_type, listing_item.export_qty_value,
                                                                         listing_id.mk_instance_id.warehouse_id.lot_stock_id, listing_id.mk_instance_id.stock_field_id.sudo().name)
                    listing_id.mk_instance_id._send_bol_request('retailer/offers/{}'.format(listing_id.mk_id),{'stock': {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True}},method='PATCH', accept=BOL_OFFERS_ACCEPT_V11)
            if operation_wizard.is_set_price:
                for listing_item in listing_id.listing_item_ids:
                    price_dict = listing_item.get_offer_price_for_bol()
                    if not price_dict or not price_dict.get('bundlePrices'):
                        continue
                    listing_id.mk_instance_id._send_bol_request('retailer/offers/{}'.format(listing_id.mk_id),{'pricing': price_dict},method='PATCH', accept=BOL_OFFERS_ACCEPT_V11)
            listing_id._bol_fetch_not_for_sale_reasons()
        return True

    def fetch_fbb_inventory_from_bol(self, mk_instance_id):
        bol_inventory_list, page = [], 0
        while True:
            page += 1
            params = {'page': page}
            response = mk_instance_id._send_bol_request('retailer/inventory', {}, params=params)
            if not response.get('inventory'):
                break
            bol_inventory_list += response['inventory']
        return bol_inventory_list

    def bol_import_fbb_stock(self, mk_instance_id, auto_validate=False):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
        try:
            bol_inventory_list = self.fetch_fbb_inventory_from_bol(mk_instance_id)
        except Exception as e:
            log_message = "IMPORT STOCK: Failed to fetch FBB inventory from Bol.com, ERROR: {}.".format(e)
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
            return mk_instance_id.action_open_model_view(mk_log_id.ids, 'mk.log', 'Bol.com Stock Log')
        quant_obj = self.env['stock.quant']
        if not mk_instance_id.bol_fbb_warehouse_id.bol_scrap_loc_id:
            log_message = "IMPORT STOCK: No location defined for bol unsaleable stock in Instance {}. Please select location from Instance > Configuration > FBB Warehouse > BOL Unsaleable Location".format(
                mk_instance_id.name)
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
            return False
        bol_scrap_loc_id = mk_instance_id.bol_fbb_warehouse_id.bol_scrap_loc_id
        date = fields.Datetime.now()
        for inventory in bol_inventory_list:
            ean = inventory.get('ean')
            regular_stock = inventory.get('regularStock', 0)
            graded_stock = inventory.get('gradedStock', 0)
            mk_listing_item_id = self.env['mk.listing.item']
            for candidate in _get_possible_barcodes(inventory.get('ean')):
                mk_listing_item_id = self.env['mk.listing.item'].search([('mk_listing_id.bol_fulfilment_method', '=', 'FBB'), ('mk_instance_id', '=', mk_instance_id.id), ('barcode', '=', candidate)], limit=1)
                if mk_listing_item_id:
                    break
            if not mk_listing_item_id:
                log_message = "IMPORT STOCK: Listing item isn't found for EAN {}".format(ean)
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                continue
            product_id = mk_listing_item_id.product_id
            if graded_stock > 0:
                quant_obj.create_or_update_inventory_quant(bol_scrap_loc_id.id, product_id, graded_stock, name="Unsellable Inventory ({} on {})".format(mk_instance_id.name, datetime.now().strftime(DF)),
                                                           auto_validate=auto_validate)
                log_message = "IMPORT STOCK: Product {} updated to {} quantity with {} location.".format(product_id.display_name, graded_stock, bol_scrap_loc_id.display_name)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message}]})
            if regular_stock > 0:
                quant_obj.create_or_update_inventory_quant(mk_instance_id.bol_fbb_warehouse_id.lot_stock_id.id, product_id, regular_stock,
                                                           name="Inventory ({} on {})".format(mk_instance_id.name, datetime.now().strftime(DF)),
                                                           auto_validate=auto_validate)
                log_message = "IMPORT STOCK: Product {} updated to {} quantity with {} location.".format(product_id.display_name, regular_stock, mk_instance_id.warehouse_id.lot_stock_id.display_name)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message}]})
        mk_instance_id.last_stock_import_date = date
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
            return True
        return mk_instance_id.action_open_model_view(mk_log_id.ids, 'mk.log', 'Bol.com Stock Log')

    def cron_auto_import_fbb_bol_stock(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.bol_import_fbb_stock(mk_instance_id, mk_instance_id.is_validate_adjustment)
        return True

    def cron_auto_export_bol_stock(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.update_stock_in_bol_ts(mk_instance_id)
        return True

    def cron_auto_export_bol_price(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.update_price_in_bol_ts(mk_instance_id)
        return True

    def get_mk_listing_item_for_price_update(self, mk_instance_id):
        if mk_instance_id.marketplace != 'bol':
            return super().get_mk_listing_item_for_price_update(mk_instance_id)
        pricelist = mk_instance_id.pricelist_id
        last_update = mk_instance_id.last_listing_price_update_date
        if last_update:
            # A rule can change the price it applies without anybody saving it: the clock simply reaches its start date, or passes its end date.
            # Those two moments leave write_date untouched, so they are looked for separately, across the whole span since the last update.
            # A rule applies from its start date up to and including its end date, so it starts applying at the start date but stops only just after the end date.
            now = fields.Datetime.now()
            changed_items = pricelist.item_ids.filtered_domain(['|', '|', ('write_date', '>', last_update), '&', ('date_start', '>', last_update), ('date_start', '<=', now),
                                                                '&', ('date_end', '>=', last_update), ('date_end', '<', now)])
        else:
            changed_items = pricelist.item_ids
        if not changed_items:
            return self.env['mk.listing.item']
        product_ids = self.env['product.product']
        all_listed = False
        for item in changed_items:
            if item.applied_on == '0_product_variant' and item.product_id:
                product_ids |= item.product_id
            elif item.applied_on == '1_product' and item.product_tmpl_id:
                product_ids |= item.product_tmpl_id.product_variant_ids
            elif item.applied_on == '2_product_category' and item.categ_id:
                product_ids |= self.env['product.product'].search([('categ_id', 'child_of', item.categ_id.id)])
            elif item.applied_on == '3_global':
                all_listed = True
                break
        if all_listed:
            return self.env['mk.listing.item'].search([('is_listed', '=', True), ('mk_instance_id', '=', mk_instance_id.id), ])
        return self.get_mk_listing_item_from_product_variants(product_ids, mk_instance_id)

    def update_price_in_bol_ts(self, mk_instance_id):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
        mk_log_line_dict = {'error': [], 'success': []}
        listing_item_ids = self.get_mk_listing_item_for_price_update(mk_instance_id)
        date = fields.Datetime.now()
        for listing_item_id in listing_item_ids:
            price_dict = listing_item_id.get_offer_price_for_bol()
            if not price_dict or not price_dict.get('bundlePrices'):
                continue
            try:
                listing_item_id.mk_instance_id._send_bol_request('retailer/offers/{}'.format(listing_item_id.mk_id),{'pricing': price_dict},method='PATCH', accept=BOL_OFFERS_ACCEPT_V11)
            except Exception as e:
                log_message = "Error while trying to update price for Bol Offer/Product : {}, ERROR: {}.".format(listing_item_id.name, e)
                mk_log_line_dict['error'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
                continue
            log_message = "The price of {} in Bol.com has been successfully updated.".format(listing_item_id.name)
            mk_log_line_dict['success'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
        mk_instance_id.last_listing_price_update_date = date
        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
        if not mk_log_id.log_line_ids:
            mk_log_id.unlink()
            return True
        return mk_instance_id.action_open_model_view(mk_log_id.ids, 'mk.log', 'Bol.com Price Log')

    def update_stock_in_bol_ts(self, mk_instance_ids):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        log_ids = self.env['mk.log']
        last_instance_id = False
        for mk_instance_id in mk_instance_ids:
            last_instance_id = mk_instance_id
            listing_item_ids = self.get_mk_listing_item(mk_instance_id)
            if isinstance(listing_item_ids, list):
                listing_item_ids = self.env['mk.listing.item'].browse(listing_item_ids)
            last_stock_update_date = fields.Datetime.now()
            if listing_item_ids:
                mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
                mk_log_line_dict = {'error': [], 'success': []}
                for listing_item in listing_item_ids.filtered(lambda x: x.mk_listing_id.bol_fulfilment_method == 'FBR' and x.product_id.is_storable):
                    quantity = listing_item.product_id.get_product_stock(listing_item.export_qty_type, listing_item.export_qty_value, mk_instance_id.warehouse_id.lot_stock_id, mk_instance_id.stock_field_id.sudo().name)
                    try:
                        mk_instance_id._send_bol_request('retailer/offers/{}'.format(listing_item.mk_id), {'stock': {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True}}, method='PATCH',
                                                         accept=BOL_OFFERS_ACCEPT_V11)
                    except Exception as e:
                        log_message = "Error while trying to export stock in Bol for Product: {}, ERROR: {}.".format(listing_item.name, e)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                        continue
                    log_message = "Successfully Updated {} Listing Stock in Bol.".format(listing_item.name)
                    mk_log_line_dict['success'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
                if not mk_log_id.log_line_ids:
                    mk_log_id.unlink()
                elif mk_log_id.exists():
                    log_ids |= mk_log_id
            mk_instance_id.last_stock_update_date = last_stock_update_date
        if log_ids and last_instance_id:
            return last_instance_id.action_open_model_view(log_ids.ids, 'mk.log', 'Bol.com Stock Log')
        return True

    def bol_export_product_limit(self):
        # Legacy hard cap removed — exports are now queue-based and chunk into
        # batches of mk_instance.queue_batch_limit. Method retained for the
        # hasattr() probe in mk.operation.export_listing_to_mk.
        return True

    def bol_prepare_export_queue(self, operation_wizard):
        """Shard listings into mk.queue.job(type='product_export') batches.
        Validation + payload build deferred to executor so failures log per-line."""
        if not self:
            return True
        instance = self[0].mk_instance_id
        batch_size = instance.queue_batch_limit or 100
        publish_flag = operation_wizard.bol_publish_in_store or 'publish'
        queue_ids = self.env['mk.queue.job']
        for batch in split_every(batch_size, self.ids):
            listings = self.browse(batch)
            queue_id = instance.action_create_queue(type='product_export')
            for listing in listings:
                queue_id.action_create_queue_lines({
                    'mk_id': str(listing.id),
                    'name': listing.name,
                    'state': 'draft',
                    'data_to_process': pprint.pformat({
                        'listing_id': listing.id,
                        'publish_flag': publish_flag,
                    }),
                    'mk_listing_id': listing.id,
                })
            queue_ids |= queue_id
        if not queue_ids:
            return True
        return instance.action_open_model_view(queue_ids.ids, 'mk.queue.job', 'Bol.com Export Queue')

    def bol_open_update_listing_view(self):
        action = self.env.ref('base_marketplace.action_listing_update_to_marketplace').sudo().read()[0]
        action['name'] = _("Update Offer in Bol.com")
        action['views'] = [(self.env.ref('bol.mk_operation_update_listing_to_bol_view').id, 'form')]
        action['context'] = self.env.context.copy()
        return action

    def bol_open_export_listing_view(self):
        action = self.env.ref('base_marketplace.action_product_export_to_marketplace').sudo().read()[0]
        action['name'] = _("Export Offer to Bol.com")
        action['views'] = [(self.env.ref('bol.mk_operation_export_listing_to_bol_view').id, 'form')]
        ctx = self._context.copy()
        ctx.setdefault('default_bol_publish_in_store', 'publish')
        action['context'] = ctx
        return action

    def bol_open_listing_in_marketplace(self):
        raise MarketplaceException(_("Navigation not available for Bol marketplace."))

    # -----------------------------------------------------------------
    # Offers API v11 helpers
    # -----------------------------------------------------------------
    def _bol_prepare_condition_payload(self):
        self.ensure_one()
        category = self.bol_condition_category or 'NEW'
        payload = {'category': category}
        if category == 'SECONDHAND':
            if not self.bol_condition_state:
                raise MarketplaceException(_("Listing %s: state required when condition category is SECONDHAND.") % self.name)
            payload['state'] = self.bol_condition_state
            if self.bol_condition_comment:
                payload['comment'] = self.bol_condition_comment
        elif category == 'REFURBISHED':
            if not self.bol_condition_grade:
                raise MarketplaceException(_("Listing %s: grade required when condition category is REFURBISHED.") % self.name)
            payload['grade'] = self.bol_condition_grade
            payload['margin'] = bool(self.bol_condition_margin)
        return payload

    def _bol_prepare_fulfilment_payload(self):
        self.ensure_one()
        if self.bol_fulfilment_method == 'FBB':
            return {'method': 'FBB'}
        if not self.bol_fulfilment_schedule:
            raise MarketplaceException(_("Listing %s: fulfilment schedule required for FBR offer.") % self.name)
        payload = {'method': 'FBR', 'schedule': self.bol_fulfilment_schedule}
        if self.bol_fulfilment_schedule == 'BOL_DELIVERY_PROMISE':
            if self.bol_delivery_max_days <= 0:
                raise MarketplaceException(_("Listing %s: max days required for BOL_DELIVERY_PROMISE.") % self.name)
            promise = {'minimumDaysToCustomer': self.bol_delivery_min_days, 'maximumDaysToCustomer': self.bol_delivery_max_days}
            if self.bol_delivery_min_days == 0 and self.bol_delivery_max_days == 1:
                if not self.bol_ultimate_order_time:
                    raise MarketplaceException(_("Listing %s: ultimate order time required for 24h promise.") % self.name)
                promise['ultimateOrderTime'] = self.bol_ultimate_order_time
            payload['deliveryPromise'] = promise
        return payload

    def _bol_prepare_offer_payload(self):
        self.ensure_one()
        listing_item = self.listing_item_ids[:1]
        if not listing_item:
            raise MarketplaceException(_("Offer '%s' needs a variant before export.") % self.name)
        price_dict = listing_item.get_offer_price_for_bol()
        if not price_dict.get('bundlePrices'):
            raise MarketplaceException(_("Offer '%s': no price found (sales price is 0 or no pricelist rule applies). Bol.com requires a price to publish an offer.") % self.name)
        quantity = listing_item.product_id.get_product_stock(listing_item.export_qty_type, listing_item.export_qty_value, self.mk_instance_id.warehouse_id.lot_stock_id, self.mk_instance_id.stock_field_id.sudo().name)
        ean = _normalize_barcode_for_bol(listing_item.barcode)
        if not ean or len(ean) != 13:
            raise MarketplaceException(_("Offer '%s': barcode '%s' cannot be normalized to a 13-digit EAN.") % (self.name, listing_item.barcode))
        payload = {
            'ean': ean,
            'condition': self._bol_prepare_condition_payload(),
            'onHoldByRetailer': True,
            'pricing': price_dict,
            'fulfilment': self._bol_prepare_fulfilment_payload(),
        }
        if listing_item.default_code:
            payload['reference'] = listing_item.default_code
        if self.bol_fulfilment_method == 'FBR':
            payload['stock'] = {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True}
        return payload

    def _bol_parse_condition_response(self, condition_dict):
        category = condition_dict.get('category')
        vals = {'bol_condition_category': category, 'bol_condition_state': False, 'bol_condition_comment': False, 'bol_condition_grade': False, 'bol_condition_margin': False}
        if category == 'SECONDHAND':
            vals['bol_condition_state'] = condition_dict.get('state')
            vals['bol_condition_comment'] = condition_dict.get('comment')
            vals['bol_condition'] = condition_dict.get('state')
        elif category == 'REFURBISHED':
            vals['bol_condition_grade'] = condition_dict.get('grade')
            vals['bol_condition_margin'] = bool(condition_dict.get('margin'))
            grade = condition_dict.get('grade')
            vals['bol_condition'] = {'A': 'AS_NEW', 'B': 'GOOD', 'C': 'MODERATE'}.get(grade, 'AS_NEW')
        else:
            vals['bol_condition'] = 'NEW'
        return vals

    def _bol_parse_fulfilment_response(self, fulfilment_dict):
        method = fulfilment_dict.get('method')
        schedule = fulfilment_dict.get('schedule')
        promise = fulfilment_dict.get('deliveryPromise') or {}
        return {
            'bol_fulfilment_method': method,
            'bol_fulfilment_schedule': schedule,
            'bol_delivery_min_days': promise.get('minimumDaysToCustomer') or 0,
            'bol_delivery_max_days': promise.get('maximumDaysToCustomer') or 0,
            'bol_ultimate_order_time': promise.get('ultimateOrderTime') or False,
        }

    # -----------------------------------------------------------------
    # Catalog (Product Content API v10)
    # Bol.com keeps product content in one catalog shared by every seller, keyed on EAN. An offer can only be sold once
    # that catalog product exists, so export checks the catalog first and asks Bol.com to build the product when it is missing.
    # -----------------------------------------------------------------
    def _bol_get_catalog_product(self, mk_instance_id, ean):
        """Ask Bol.com whether it knows this barcode."""
        if not ean:
            return 'missing', False
        try:
            response = mk_instance_id.with_context(skip_raise=True)._send_bol_request('retailer/content/catalog-products/{}'.format(ean), {}, method='GET', accept=BOL_ACCEPT_V10)
        except Exception as e:
            _logger.warning("EXPORT PRODUCT: Could not read the Bol.com catalog | EAN: %s | Error: %s", ean, e)
            return 'error', e
        if isinstance(response, dict):
            return 'found', response
        return ('missing', False) if response is True else ('error', False)

    def _bol_get_catalog_title(self, mk_instance_id, ean):
        """The product title bol.com publishes for this barcode, together with the outcome of the call.

        Returns ('found'|'missing'|'error', title). A barcode bol.com does not know is a final answer;
        a call that failed is not, and the caller may want to try it again later.
        """
        catalog_state, catalog_product = self._bol_get_catalog_product(mk_instance_id, ean)
        if catalog_state != 'found' or not isinstance(catalog_product, dict):
            return catalog_state, False
        for attribute in catalog_product.get('attributes') or []:
            if attribute.get('id') == 'Title':
                return catalog_state, ((attribute.get('values') or [{}])[0] or {}).get('value') or False
        return catalog_state, False

    def _bol_name_is_placeholder(self):
        """True when the name is one the connector generated (SKU or barcode), not one the customer chose."""
        self.ensure_one()
        listing_item_id = self.listing_item_ids[:1]
        return not self.name or self.name in (listing_item_id.default_code, listing_item_id.barcode, self.mk_id)

    def _bol_sync_name_from_catalog(self):
        """Take over the name bol.com publishes for this barcode, so the offer is not left under its SKU or EAN."""
        self.ensure_one()
        listing_item_id = self.listing_item_ids[:1]
        ean = _normalize_barcode_for_bol(listing_item_id.barcode) or listing_item_id.barcode
        catalog_state, catalog_title = self._bol_get_catalog_title(self.mk_instance_id, ean)
        if not catalog_title:
            return catalog_state
        if self.name != catalog_title:
            self.name = catalog_title
        if listing_item_id and listing_item_id.name != catalog_title:
            listing_item_id.name = catalog_title
        return catalog_state

    def _bol_get_gpc_chunk_id(self, product_title):
        """Ask Bol.com which product classification fits this title. Returns the code only when Bol.com is confident."""
        self.ensure_one()
        request_vals = {'productContents': [{'attributes': [{'id': 'Name', 'values': [{'value': product_title}]}]}]}
        try:
            response = self.mk_instance_id._send_bol_request('retailer/content/chunk-recommendations', request_vals, method='POST', accept=BOL_ACCEPT_V10)
        except Exception as e:
            _logger.warning("EXPORT PRODUCT: Could not get a Bol.com category | Title: %s | Error: %s", product_title, e)
            return False
        predictions = []
        for recommendation in (isinstance(response, dict) and response.get('recommendations') or []):
            predictions += recommendation.get('predictions') or []
        best_prediction = max(predictions, key=lambda prediction: prediction.get('probability') or 0, default=False)
        if not best_prediction or (best_prediction.get('probability') or 0) < BOL_GPC_MIN_PROBABILITY:
            return False
        return best_prediction.get('chunkId')

    def _bol_log_catalog(self, log_message, error=False):
        """Catalog work happens during export, so it is reported in the export log the customer already reads."""
        self.ensure_one()
        key = 'error' if error else 'success'
        self.env['mk.log'].create_update_log(mk_instance_id=self.mk_instance_id, mk_log_id=self.env.context.get('mk_log_id', False), operation_type='export',
                                             mk_log_line_dict={key: [{'log_message': 'EXPORT PRODUCT: {}'.format(log_message)}]})
        return True

    def _bol_create_catalog_product(self, ean, product_title):
        """Send the minimum content Bol.com needs to build the catalog product. Returns True when Bol.com accepted the job."""
        self.ensure_one()
        chunk_id = self._bol_get_gpc_chunk_id(product_title)
        if not chunk_id:
            self._bol_log_catalog(_("Bol.com could not determine a category for '%s', so no catalog product was created. Add the product on Bol.com and export again.") % product_title, error=True)
            return False
        request_vals = {
            # Bol.com only takes the language of the country it sells in, so it follows the instance's country.
            'language': BOL_CONTENT_LANGUAGE_BY_COUNTRY.get(self.mk_instance_id.country_id.code, 'nl'),
            'attributes': [
                {'id': 'EAN', 'values': [{'value': ean}]},
                {'id': 'GPC Code', 'values': [{'value': chunk_id}]},
                {'id': 'Title', 'values': [{'value': product_title[:500]}]},
            ],
        }
        try:
            response = self.mk_instance_id._send_bol_request('retailer/content/products', request_vals, method='POST', accept=BOL_ACCEPT_V10)
        except Exception as e:
            self._bol_log_catalog(_("Bol.com refused the catalog product for '%s': %s") % (product_title, e), error=True)
            return False
        if not (isinstance(response, dict) and response.get('processStatusId')):
            self._bol_log_catalog(_("Bol.com did not start a catalog job for '%s': %s") % (product_title, response), error=True)
            return False
        process_id = self.env['bol.process.status'].create_or_update_process_status(response, self.mk_instance_id)
        self.bol_content_process_id = process_id.id
        self._bol_log_catalog(_("Catalog product requested at Bol.com for '%s' (EAN %s). The offer can be sold once Bol.com has published it.") % (product_title, ean))
        return True

    def _bol_prepare_catalog_for_export(self, ean):
        """Catalog first: make sure Bol.com has a product for this barcode before the offer is sent."""
        self.ensure_one()
        catalog_state, _catalog_product = self._bol_get_catalog_product(self.mk_instance_id, ean)
        if catalog_state == 'found':
            return True
        if catalog_state == 'error':
            # Bol.com may well know this barcode; creating content now could overwrite another seller's product.
            self._bol_log_catalog(_("Bol.com could not be asked about barcode %s, so no catalog product was created.") % ean, error=True)
            return False
        listing_item_id = self.listing_item_ids[:1]
        product_title = listing_item_id.name or listing_item_id.product_id.display_name or self.name
        self._bol_create_catalog_product(ean, product_title)
        return False

    def _bol_check_catalog_job(self):
        """Read the finished catalog job and report what Bol.com made of the product content."""
        self.ensure_one()
        process_id = self.bol_content_process_id
        if not process_id:
            return False
        if process_id.state != 'success':
            process_id.get_process_status()
        if process_id.state in ('failure', 'timeout'):
            self._bol_log_catalog(_("Bol.com could not build the catalog product for %s: %s") % (self.name, process_id.error_message or process_id.state), error=True)
            self.bol_content_process_id = False
            return False
        if process_id.state != 'success' or not process_id.entity_id:
            return False
        try:
            report = self.mk_instance_id._send_bol_request('retailer/content/upload-report/{}'.format(process_id.entity_id), {}, method='GET', accept=BOL_ACCEPT_V10)
        except Exception as e:
            _logger.warning("EXPORT PRODUCT: Could not read the Bol.com upload report | Listing: %s | Error: %s", self.name, e)
            return False
        # The report itself says whether Bol.com is done (IN_PROGRESS or COMPLETED); the attributes say what came of it.
        if not isinstance(report, dict) or report.get('status') != 'COMPLETED':
            return False
        attributes = report.get('attributes') or []
        declined_lines = ["{}: {}".format(attribute.get('id'), attribute.get('subStatusDescription') or attribute.get('subStatus') or '') for attribute in attributes if attribute.get('status') == 'DECLINED']
        if declined_lines:
            self._bol_log_catalog(_("Bol.com declined the catalog product for %s: %s") % (self.name, ', '.join(declined_lines)), error=True)
        else:
            # Bol.com sells the offer by itself now that the product exists, so only the reasons on the listing need a refresh.
            self._bol_log_catalog(_("Bol.com published the catalog product for %s, so the offer can be sold.") % self.name)
            self._bol_fetch_not_for_sale_reasons()
        self.bol_content_process_id = False
        return True

    def _bol_fetch_not_for_sale_reasons(self):
        """Diagnostic call. Populates bol_not_publishable_reasons. Best-effort, never raises."""
        for listing in self:
            if not listing.mk_id:
                continue
            lines = self._fetch_not_for_sale_reasons(listing.mk_instance_id, listing.mk_id)
            listing.bol_not_publishable_reasons = '\n'.join(lines) or False
        return True
