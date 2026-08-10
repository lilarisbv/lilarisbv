import logging
from odoo import api, fields, models

_logger = logging.getLogger("Teqstars:WooCommerce")


class WooCommerceCustomFieldMapping(models.Model):
    _name = "woocommerce.custom.field.mapping"
    _description = "WooCommerce Custom Field Mapping"

    woo_custom_key = fields.Char('Custom Field Name')
    woo_operation = fields.Selection([
        ('import_customer', 'Import Customers'),
        ('import_listing', 'Import Listings'),
        ('import_order', 'Import Orders'),
        ('update_order_status', "Export Tracking details")], 'Operations')
    model_id = fields.Many2one('ir.model', ondelete='cascade')
    field_id = fields.Many2one('ir.model.fields', ondelete='cascade')
    field_type = fields.Char(compute="_compute_field_type")
    date_format = fields.Char(string='Date Format')
    time_format = fields.Char(string='Time Format')
    mk_instance_id = fields.Many2one("mk.instance", copy=False, ondelete="cascade")

    _custom_field_mapping_unique_constraint = models.Constraint(
        "unique(woo_custom_key, mk_instance_id, woo_operation, model_id)", "Custom Field Name, Operation, and Model must be unique in the Custom Field Mapping per WooCommerce Instance."
    )

    @api.depends('field_id')
    def _compute_field_type(self):
        for record in self:
            record.field_type = record.field_id.ttype

    @api.onchange('woo_operation')
    def _onchange_operation(self):
        model_list = []
        if self.woo_operation == 'import_listing':
            model_list = ['product.template', 'product.product']
        elif self.woo_operation == 'import_customer':
            model_list = ['res.partner']
        elif self.woo_operation == 'import_order':
            model_list = ['res.partner', 'sale.order', 'stock.picking']
        elif self.woo_operation == 'update_order_status':
            model_list = ['stock.picking', 'sale.order']
        return {'domain': {'model_id': [('model', 'in', model_list)]}}

    @api.onchange('field_id')
    def _onchange_field(self):
        if self.field_id.ttype in ['datetime', 'date']:
            lang = self.mk_instance_id.lang if self.mk_instance_id else 'en_US'
            res_lang = self.env['res.lang'].search([('code', '=', lang)], limit=1)
            self.time_format = res_lang.time_format if self.field_id.ttype == 'datetime' else False
            self.date_format = res_lang.date_format
        else:
            self.time_format = False
            self.date_format = False
