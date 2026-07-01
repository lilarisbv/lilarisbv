from odoo import models, fields, api, _


# 1. YARDIM DÖKÜMANI MODELİ
class CustomerTicketHelp(models.Model):
    _name = 'customer.ticket.help'
    _description = 'Customer Ticket Help Guide'
    # İçerik statik XML olacağı için field gerekmiyor.

    name = fields.Char(string="Title", default="Help & Guide", translate=True)


# 2. ANA BİLET MODELİNİZE EKLENECEK BUTON FONKSİYONU
class CustomerTicket(models.Model):
    _inherit = 'customer.ticket'

    def action_open_help_page(self):
        """Yardım butonuna basıldığında dökümanı tam ekran (target='current') açar"""
        # Sistemde XML ile yarattığımız sabit kaydı buluyoruz
        help_record = self.env.ref('customer_ticket.customer_ticket_help_static_data')

        return {
            'name': _('Help & Guide'),
            'type': 'ir.actions.act_window',
            'res_model': 'customer.ticket.help',
            'view_mode': 'form',
            'res_id': help_record.id,  # Sabit kaydın ID'si
            'view_id': self.env.ref('customer_ticket.view_customer_ticket_help_form').id,
            'target': 'current',  # Tam ekran açılması için akışa dahil ettik
        }