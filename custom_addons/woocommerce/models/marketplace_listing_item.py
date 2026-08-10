from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

TAX_STATUS = [('taxable', 'Taxable'),
              ('shipping', 'Shipping Only'),
              ('none', 'None')]


class MkListingItem(models.Model):
    _inherit = "mk.listing.item"

    woo_tax_status = fields.Selection(TAX_STATUS, 'Tax Status', default='none', help='Define whether or not the entire product is taxable, or just the cost of shipping it.')
    woo_tax_class = fields.Char("Code", help='Choose a tax class for this product. Tax classes are used to apply different tax rates specific to certain types of product.')
    woo_manage_stock = fields.Boolean('Manage Stock?', default=True, help='Enable Stock management at variation level.')
    woocommerce_last_stock_update_date = fields.Datetime("Last Stock Updated On", copy=False, help="Date were stock updated to WooCommerce.")

    def write(self, values):
        res = super(MkListingItem, self).write(values)
        for record in self:
            if values.get('image_ids') and len(record.image_ids) > 1 and record.mk_instance_id.marketplace == 'woocommerce':
                raise ValidationError(_("WooCommerce only accept one image per variant"))
        return res

    def woocommerce_hide_fields(self):
        return ['is_taxable', 'barcode']

    def woocommerce_hide_page(self):
        return []
