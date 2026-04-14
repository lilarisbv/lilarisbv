import logging
from datetime import datetime

from odoo import models, fields, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DF

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


class MkListing(models.Model):
    _inherit = "mk.listing"

    bol_condition = fields.Selection(BOL_CONDITION, string='Condition', default="NEW", help='The condition of the offered product. e.g., new or second hand product')
    bol_delivery_code = fields.Selection(BOL_DELIVERY_CODES, string='Promised delivery time', help='The delivery promise that applies to this offer.')
    bol_fulfilment_method = fields.Selection(BOL_FULFILMENT_METHOD, string='Fulfilment Method', default='FBR',
                                             help='Specifies whether this shipment has been fulfilled by the retailer (FBR) or fulfilled by bol.com (FBB). Defaults to FBR.')
    bol_not_publishable_reasons = fields.Text('Not Publishable Reasons', help="Error message describing the reason the offer is invalid.")
    bol_onhold_by_retailer = fields.Boolean('On Hold', help="Indicates whether or not you want to put this offer for sale on the bol.com website. Defaults to false.")

    def bol_hide_fields(self):
        return ['product_category_id', 'listing_create_date', 'listing_update_date', 'listing_publish_date']

    def bol_hide_page(self):
        return ['description', 'product_images']

    def put_listing_on_hold(self):
        request_vals = {'onHoldByRetailer': True, 'fulfilment': {'method': self.bol_fulfilment_method, 'deliveryCode': self.bol_delivery_code}}
        response = self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(self.mk_id), request_vals, method="PUT")
        self.env['bol.process.status'].create_or_update_process_status(response, self.mk_instance_id)
        self.write({'is_published': False})
        return True

    def put_listing_on_unhold(self):
        request_vals = {'onHoldByRetailer': False, 'fulfilment': {'method': self.bol_fulfilment_method, 'deliveryCode': self.bol_delivery_code}}
        response = self.mk_instance_id._send_bol_request('retailer/offers/{}'.format(self.mk_id), request_vals, method="PUT")
        self.env['bol.process.status'].create_or_update_process_status(response, self.mk_instance_id)
        self.write({'is_published': True})
        return True

    def bol_published(self):
        if not self.is_published:
            self.put_listing_on_unhold()
        else:
            self.put_listing_on_hold()
        return True

    def create_odoo_template_for_bol_product(self, bol_offer_dict, existing_odoo_product, mk_instance_id, update_product_price):
        odoo_template_obj = self.env["product.template"]
        product_title = bol_offer_dict.get('store', {}).get('productTitle') or bol_offer_dict.get('reference')
        product_template_vals = {
            'name': product_title,
            'type': 'consu',
            'is_storable': True,
            'default_code': bol_offer_dict.get('reference'),
        }
        product_tmpl_id = odoo_template_obj.create(product_template_vals)
        product_tml_update_vals = {}
        if bol_offer_dict.get('ean'):
            product_tml_update_vals.update({'barcode': bol_offer_dict.get('ean')})
        bundle_pricing = bol_offer_dict.get('pricing', {}).get('bundlePrices')
        price = 0.0
        for price_dict in bundle_pricing:
            quantity, bol_price = price_dict.get('quantity'), price_dict.get('price')
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
        existing_odoo_product.update({bol_offer_dict.get('offerId'): product_tmpl_id.product_variant_ids})
        return product_tmpl_id

    def prepare_marketplace_listing_vals_for_bol(self, bol_offer_dict, mk_instance_id, odoo_product_id):
        vals = {}
        mk_id = bol_offer_dict.get('offerId')
        offer_title = bol_offer_dict.get('store', {}).get('productTitle') or bol_offer_dict.get('reference')
        not_publishable_reasons = ['[{}] - {}'.format(reason.get('code'), reason.get('description')) for reason in bol_offer_dict.get('notPublishableReasons', [])]
        vals.update({
            'name': offer_title,
            'mk_instance_id': mk_instance_id.id,
            'product_tmpl_id': odoo_product_id.product_tmpl_id.id,
            'mk_id': mk_id,
            'listing_create_date': False,
            'listing_update_date': False,
            'listing_publish_date': False,
            'is_published': not not_publishable_reasons,
            'is_listed': True,
            'bol_onhold_by_retailer': bol_offer_dict.get('onHoldByRetailer'),
            'bol_fulfilment_method': bol_offer_dict.get('fulfilment', {}).get('method'),
            'bol_delivery_code': bol_offer_dict.get('fulfilment', {}).get('deliveryCode'),
            'bol_not_publishable_reasons': '\n'.join(not_publishable_reasons),
            'bol_condition': bol_offer_dict.get('condition', {}).get('name'),
        })
        return vals

    def prepare_marketplace_listing_item_vals_for_bol(self, bol_offer_dict, mk_instance_id, odoo_product_id, mk_listing_id):
        offer_title = bol_offer_dict.get('store', {}).get('productTitle') or bol_offer_dict.get('reference')
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
        offer_title = bol_offer_dict.get('store', {}).get('productTitle') or variant_sku
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

        validated, log_message = self.check_validation_for_import_product(mk_instance_id.sync_product_with, listing_item_validation_dict, odoo_product_template, existing_odoo_product, existing_mk_product)
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
            offer_response = mk_instance_id._send_bol_request('retailer/offers/{}'.format(mk_listing_id), {})
            mk_listing_id = self.with_context(mk_log_line_dict=mk_log_line_dict, mk_log_id=mk_log_id).create_update_bol_product(offer_response, mk_instance_id, update_product_price=True)
            if mk_log_line_dict.get('error'):
                raise MarketplaceException(_("Found problem during Import listing, Details are below.\n{}".format(
                    ',\n'.join([error_dict.get('log_message') for error_dict in mk_log_line_dict.get('error')]))))
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
            return mk_listing_id
        response = mk_instance_id._send_bol_request('retailer/offers/export', {'format': 'CSV'}, method="POST")
        process_id = self.env['bol.process.status'].create_or_update_process_status(response, mk_instance_id, update_product_price=update_product_price, update_existing_product=update_existing_product)
        while True:
            process_id.get_process_status()
            if process_id.state == 'success' and process_id.entity_id:
                break
            if process_id.state in ['failure', 'timeout']:
                raise MarketplaceException(_("Failed to Retrieve Offers. STATUS: {} ERROR: {}".format(process_id.state.upper(), process_id.error_message)))
        process_id.do_fetch_offer_data()
        # return {
        #     'name': process_id.display_name,
        #     'type': 'ir.actions.act_window',
        #     'view_mode': 'form',
        #     'res_model': 'bol.process.status',
        #     'res_id': process_id.id,
        #     'target': 'current',
        # }

    def bol_export_listing_to_mk(self, operation_wizard):
        for listing_id in self:
            if not listing_id.listing_item_ids:
                raise MarketplaceException(_("Offer '{}' should have at least one variant for export".format(listing_id.name)))
            if len(listing_id.listing_item_ids) > 1:
                raise MarketplaceException(_("Offer '{}' shouldn't have multiple variants.".format(listing_id.name)))
            if not listing_id.listing_item_ids.barcode:
                raise MarketplaceException(_("Offer '{}' must have barcode defined in variant tab's product.".format(listing_id.name)))
            price_dict = listing_id.listing_item_ids.get_offer_price_for_bol()
            quantity = listing_id.listing_item_ids.product_id.get_product_stock(listing_id.listing_item_ids.export_qty_type, listing_id.listing_item_ids.export_qty_value, listing_id.listing_item_ids.mk_instance_id.warehouse_id.lot_stock_id,listing_id.listing_item_ids.mk_instance_id.stock_field_id.name)
            request_vals = {'ean': listing_id.listing_item_ids.barcode,
                            'condition': {'name': listing_id.bol_condition},
                            'reference': listing_id.listing_item_ids.default_code,
                            'onHoldByRetailer': True,
                            'pricing': price_dict,
                            'stock': {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True},
                            'fulfilment': {'method': listing_id.bol_fulfilment_method, 'deliveryCode': listing_id.bol_delivery_code}}
            response = listing_id.mk_instance_id._send_bol_request('retailer/offers', request_vals, method="POST")
            _logger.info("EXPORT PRODUCT: \nRequest Data: {} \nReceived Response: {}".format(request_vals, response))
            process_id = listing_id.env['bol.process.status'].create_or_update_process_status(response, listing_id.mk_instance_id)
            while True:
                process_id.get_process_status()
                if process_id.state == 'success' and process_id.entity_id:
                    listing_id.write({'is_listed': True, 'is_published': False, 'mk_id': process_id.entity_id})
                    listing_id.listing_item_ids.write({'is_listed': True, 'mk_id': process_id.entity_id})
                    _logger.info("EXPORT PRODUCT: Process Status get Success, marking listing listed in Odoo for Offer ID: {}.".format(process_id.entity_id))
                    break
                if process_id.state in ['failure', 'timeout']:
                    raise MarketplaceException(_("Failed to Export Offer '{}'. \nSTATUS: {} ERROR: {}".format(listing_id.name, process_id.state.upper(), process_id.error_message)))
            listing_id.env.cr.commit()
        return True

    def bol_update_listing_to_mk(self, operation_wizard):
        for listing_id in self:
            if operation_wizard.is_update_product:
                request_vals = {'fulfilment': {'method': listing_id.bol_fulfilment_method, 'deliveryCode': listing_id.bol_delivery_code}}
                if operation_wizard.bol_publish_in_store:
                    request_vals.update({'onHoldByRetailer': operation_wizard.bol_publish_in_store == 'hold'})
                response = listing_id.mk_instance_id._send_bol_request('retailer/offers/{}'.format(listing_id.mk_id), request_vals, method="PUT")
                self.env['bol.process.status'].create_or_update_process_status(response, listing_id.mk_instance_id)
            if operation_wizard.is_set_quantity:
                for listing_item in listing_id.listing_item_ids.filtered(lambda x: x.mk_listing_id.bol_fulfilment_method == 'FBR' and x.product_id.is_storable):
                    quantity = listing_item.product_id.get_product_stock(listing_item.export_qty_type, listing_item.export_qty_value,
                                                                         listing_id.mk_instance_id.warehouse_id.lot_stock_id, listing_id.mk_instance_id.stock_field_id.name)
                    response = listing_id.mk_instance_id._send_bol_request('retailer/offers/{}/stock'.format(listing_id.mk_id), {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True},
                                                                           method="PUT")
                    self.env['bol.process.status'].create_or_update_process_status(response, listing_id.mk_instance_id)
            if operation_wizard.is_set_price:
                for listing_item in listing_id.listing_item_ids:
                    price_dict = listing_item.get_offer_price_for_bol()
                    if not price_dict or not price_dict.get('bundlePrices'):
                        continue
                    response = listing_id.mk_instance_id._send_bol_request('retailer/offers/{}/price'.format(listing_id.mk_id), {'pricing': price_dict}, method="PUT")
                    self.env['bol.process.status'].create_or_update_process_status(response, listing_id.mk_instance_id)
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
        bol_inventory_list = self.fetch_fbb_inventory_from_bol(mk_instance_id)
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
            mk_listing_item_id = self.env['mk.listing.item'].search([('mk_listing_id.bol_fulfilment_method', '=', 'FBB'), ('mk_instance_id', '=', mk_instance_id.id), ('barcode', '=', inventory.get('ean'))], limit=1)
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
                quant_obj.create_or_update_inventory_quant(mk_instance_id.bol_fbb_warehouse_id.lot_stock_id.id, product_id, regular_stock, name="Inventory ({} on {})".format(mk_instance_id.name, datetime.now().strftime(DF)),
                                                           auto_validate=auto_validate)
                log_message = "IMPORT STOCK: Product {} updated to {} quantity with {} location.".format(product_id.display_name, regular_stock, mk_instance_id.warehouse_id.lot_stock_id.display_name)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message}]})
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        mk_instance_id.last_stock_import_date = date
        return True

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
                response = listing_item_id.mk_instance_id._send_bol_request('retailer/offers/{}/price'.format(listing_item_id.mk_id), {'pricing': price_dict}, method="PUT")
            except Exception as e:
                log_message = "Error while trying to update price for Bol Offer/Product : {}, ERROR: {}.".format(listing_item_id.name, e)
                mk_log_line_dict['error'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
                continue
            log_message = "The price of {} in Bol.com has been successfully updated.".format(listing_item_id.name)
            mk_log_line_dict['success'].append({'log_message': 'UPDATE PRICE: {}'.format(log_message)})
            self.env['bol.process.status'].create_or_update_process_status(response, listing_item_id.mk_instance_id)
        mk_instance_id.last_listing_price_update_date = date
        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
        if not mk_log_id.log_line_ids:
            mk_log_id.unlink()
        return True

    def update_stock_in_bol_ts(self, mk_instance_ids):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            listing_item_ids = self.get_mk_listing_item(mk_instance_id)
            if isinstance(listing_item_ids, list):
                listing_item_ids = self.env['mk.listing.item'].browse(listing_item_ids)
            last_stock_update_date = fields.Datetime.now()
            if listing_item_ids:
                mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
                mk_log_line_dict = {'error': [], 'success': []}
                for listing_item in listing_item_ids.filtered(lambda x: x.mk_listing_id.bol_fulfilment_method == 'FBR' and x.product_id.is_storable):
                    quantity = listing_item.product_id.get_product_stock(listing_item.export_qty_type, listing_item.export_qty_value, mk_instance_id.warehouse_id.lot_stock_id, mk_instance_id.stock_field_id.name)
                    try:
                        mk_instance_id._send_bol_request('retailer/offers/{}/stock'.format(listing_item.mk_id), {'amount': int(min(quantity, 999)) if quantity >= 0 else 0, 'managedByRetailer': True}, method="PUT")
                    except Exception as e:
                        log_message = "Error while trying to export stock in Bol for Product: {}, ERROR: {}.".format(listing_item.name, e)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                        continue
                    log_message = "Successfully Updated {} Listing Stock in Bol.".format(listing_item.name)
                    mk_log_line_dict['success'].append({'log_message': 'UPDATE STOCK: {}'.format(log_message)})
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
                if not mk_log_id.log_line_ids:
                    mk_log_id.unlink()
            mk_instance_id.last_stock_update_date = last_stock_update_date
        return True

    def bol_export_product_limit(self):
        max_limit = 80
        if self and len(self) > max_limit:
            raise MarketplaceException(_("System will not permits to send out more then 80 items all at once. Please select just 80 items for export."))
        return True

    def bol_open_update_listing_view(self):
        action = self.env.ref('base_marketplace.action_listing_update_to_marketplace').read()[0]
        action['name'] = _("Update Offer in Bol.com")
        action['views'] = [(self.env.ref('bol.mk_operation_update_listing_to_bol_view').id, 'form')]
        action['context'] = self.env.context.copy()
        return action

    def bol_open_export_listing_view(self):
        action = self.env.ref('base_marketplace.action_product_export_to_marketplace').read()[0]
        action['name'] = _("Export Offer to Bol.com")
        action['views'] = [(self.env.ref('bol.mk_operation_export_listing_to_bol_view').id, 'form')]
        action['context'] = self.env.context.copy()
        return action

    def bol_open_listing_in_marketplace(self):
        raise MarketplaceException(_("Navigation not available for Bol marketplace."))
