from odoo import fields, models


class BolTranporterCode(models.Model):
    _name = 'bol.transporter.code'
    _description = "Bol Transporters"

    name = fields.Char(string='Name', required=True)
    code = fields.Char(string='Code', required=True)
    tracking_ids = fields.One2many('bol.transporter.tracking', 'transporter_id', string="Tracking(s)")

    _name_code_unique = models.Constraint(
        'UNIQUE(name, code)',
        "Transporter name and code should be unique!",
    )

    # Imported all transporter according to Appendix A – Transporters on https://api.bol.com/retailer/public/Retailer-API/v6/functional/orders-shipments.html#_adding_transport_information_to_shipment


class BolTransporterTracking(models.Model):
    _name = 'bol.transporter.tracking'
    _description = "Bol Transporters"

    transporter_id = fields.Many2one("bol.transporter.code", string="Transporter", required=True, ondelete="cascade")
    country_ids = fields.Many2many('res.country', 'transporter_tracking_country_rel', 'tracking_id', 'country_id', 'Countries')
    tracking_url = fields.Char(string="Tracking URL")
