from odoo import fields, models


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    def _compute_is_show_bol_scrap_loc(self):
        for warehouse in self:
            is_show_bol_scrap_loc = False
            if self.env['mk.instance'].search([('marketplace', '=', 'bol'), ('bol_operation_type', 'in', ['FBB', 'Both']), ('state', '=', 'confirmed'), ('bol_fbb_warehouse_id', '=', warehouse.id)]):
                is_show_bol_scrap_loc = True
            warehouse.is_show_bol_scrap_loc = is_show_bol_scrap_loc

    is_show_bol_scrap_loc = fields.Boolean(compute="_compute_is_show_bol_scrap_loc", string="Is show Bol scrap location?", help="Technical field to hide bol scrap location field from warehouse form view.")
    bol_scrap_loc_id = fields.Many2one('stock.location', 'BOL Unsaleable Location', check_company=True)
