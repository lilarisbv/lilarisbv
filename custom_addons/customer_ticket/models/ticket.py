import requests
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
alias = "lilaris"

class CustomerTicket(models.Model):
    _name = 'customer.ticket'
    _description = 'Customer Ticket'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Subject', required=True, tracking=True)
    description = fields.Html(string='Description', required=True)

    is_urgent = fields.Boolean(string='Urgent', default=False, tracking=True)
    active = fields.Boolean(string='Active', default=True, tracking=True)

    stage = fields.Selection([
        ('draft', 'New'),
        ('queue','Queued'),
        ('process', 'In Progress'),
        ('on_hold', 'On Hold'),
        ('review', 'Under Review'),
        ('delivery', 'Delivered'),
        ('change', 'Change Requested'),
        ('done', 'Approved'),
        ('cancel', 'Cancelled')
    ], string='Stage', default='draft', tracking=True, readonly=True)

    central_task_id = fields.Integer(string='Central Task ID', readonly=True)
    ticket_number = fields.Char(string='Ticket No', readonly=True, copy=False, default='New')

    confirmation_status = fields.Char(string='Support Note', readonly=True)
    attachment_file = fields.Binary(string='Attachment / Screenshot', attachment=True)
    attachment_filename = fields.Char(string='Filename')

    contact_person_name = fields.Char(string='Contact Person', readonly=True)
    contact_person_email = fields.Char(string='Contact Email', readonly=True)
    contact_person_calendar_link = fields.Char(string='Calendar Link', readonly=True)

    @api.model
    def create(self, vals_list):
        today = fields.Date.today()
        daily_count = self.search_count([
            ('create_uid', '=', self.env.user.id),
            ('create_date', '>=', today)
        ])
        if daily_count + len(vals_list) > 10:
            raise ValidationError(_("Security Limit: You cannot open more than 10 tickets per day."))

        # Eğer vals_list bir liste ise, her bir sözlük için döngüye gir
        if isinstance(vals_list, dict):
            vals_list = [vals_list]

        for vals in vals_list:
            if vals.get('attachment_file'):
                file_size = (len(vals['attachment_file']) * 3) / 4
                max_size = 2 * 1024 * 1024  # 2 Megabayt
                if file_size > max_size:
                    raise ValidationError(_("File size too large! Screenshots must be a maximum of 2MB."))

            if vals.get('ticket_number', 'New') == 'New':
                vals['ticket_number'] = self.env['ir.sequence'].next_by_code('customer.ticket') or 'TCK-000'

        tickets = super(CustomerTicket, self).create(vals_list)
        central_url = self.env['ir.config_parameter'].sudo().get_param('central_odoo.url',
                                                                       'https://panel.nametech.be/api/ticket/create')
        api_token = self.env['ir.config_parameter'].sudo().get_param('central_odoo.token', 'Ticket2026')


        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_token}'
        }

        for ticket in tickets:
            # Payload wrapped in 'params' for Odoo JSON-RPC routing
            payload = {
                "params": {
                    "title": ticket.name,
                    "description": ticket.description,
                    "is_urgent": ticket.is_urgent,
                    "customer_ref": alias,
                    "remote_ticket_id": ticket.id,
                    "attachment_file": ticket.attachment_file.decode('utf-8') if ticket.attachment_file else False,
                    "attachment_filename": ticket.attachment_filename or "ekran_goruntusu.png"
                }
            }
            try:
                response = requests.post(central_url, json=payload, headers=headers, timeout=10)
                if response.status_code == 200:
                    result = response.json().get('result', {})
                    if result.get('status') == 'success':
                        ticket.central_task_id = result.get('central_task_id')
                    else:
                        _logger.error(f"Central Odoo API Error: {result.get('message')}")
            except Exception as e:
                _logger.error(f"Failed to push ticket to Central Odoo: {str(e)}")

        return tickets

    def _call_central_api_respond(self, action, reason=""):
        central_url = self.env['ir.config_parameter'].sudo().get_param('central_odoo.url_respond',
                                                                       'https://panel.nametech.be/api/ticket/respond')
        api_token = self.env['ir.config_parameter'].sudo().get_param('central_odoo.token', 'RespondTicket2026')
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_token}'}
        payload = {
            "params": {
                "central_task_id": self.central_task_id,
                "action": action,
                "reason": reason
            }
        }
        try:
            requests.post(central_url, json=payload, headers=headers, timeout=10)
        except Exception as e:
            _logger.error(f"Failed to send response to Central Odoo: {str(e)}")


    def action_approve(self):
        """Trigger to send approval to Central Odoo"""
        self.write({'stage': 'done'})
        self._call_central_api_respond('approve')

    def action_open_change_wizard(self):
        return {
            'name': 'Request Change',
            'type': 'ir.actions.act_window',
            'res_model': 'customer.ticket.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_ticket_id': self.id, 'default_action_type': 'change'}
        }

    def action_open_cancel_wizard(self):
        return {
            'name': 'Cancel Ticket',
            'type': 'ir.actions.act_window',
            'res_model': 'customer.ticket.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_ticket_id': self.id, 'default_action_type': 'cancel'}
        }

    def _send_customer_stage_email_notification(self, new_stage):
        """Aşama on_hold veya delivery olduğunda müşteriye mail fırlatan yardımcı fonksiyon"""
        self.ensure_one()
        template_ref = False

        if new_stage == 'on_hold':
            template_ref = 'customer_ticket.email_template_ticket_on_hold'
        elif new_stage == 'delivery':
            template_ref = 'customer_ticket.email_template_ticket_delivery'

        if template_ref:
            template = self.env.ref(template_ref, raise_if_not_found=False)
            if template and self.create_uid.email:
                template.send_mail(self.id, force_send=True)

    @api.model
    def cron_pull_stage_updates(self, *args, **kwargs):
        """Scheduled action logic to pull stage modifications from Central Odoo"""
        active_tickets = self.search([('stage', 'not in', ['done', 'cancel'])])
        if not active_tickets:
            return {'type': 'ir.actions.client', 'tag': 'reload'}

        central_url = self.env['ir.config_parameter'].sudo().get_param('central_odoo.url_get_stage',
                                                                       'https://panel.nametech.be/api/ticket/get_stage')
        api_token = self.env['ir.config_parameter'].sudo().get_param('central_odoo.token', 'TicketStage2026')
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_token}'}

        stage_name_mapping = {
            'talep': 'draft',
            'to-do': 'queue',
            'islemde': 'process',
            'beklemede': 'on_hold',
            'kontrol/mutaala': 'review',
            'teslim': 'delivery'
        }

        for ticket in active_tickets:
            if not ticket.central_task_id:
                continue

            payload = {"params": {"central_task_id": ticket.central_task_id}}
            try:
                response = requests.post(central_url, json=payload, headers=headers, timeout=5)
                if response.status_code == 200:
                    result = response.json().get('result', {})
                    if result.get('status') == 'success':

                        central_stage = result.get('stage_name', '').lower().strip()
                        central_state = result.get('state')
                        is_active = result.get('active', True)
                        central_note = result.get('client_note', '')
                        rep_name = result.get('rep_name', False)
                        rep_email = result.get('rep_email', False)
                        rep_calendar = result.get('rep_calendar_link', False)

                        target_stage = False

                        # 1. Check State and Active parameters first (Higher Priority)
                        if not is_active and central_state != '1_done':
                            target_stage = 'cancel'
                        elif central_state == '1_done':
                            target_stage = 'done'
                        elif central_state == '02_changes_requested':
                            target_stage = 'change'

                        # 2. If no special state override, map via Central Kanban Stage Name
                        if not target_stage:
                            target_stage = stage_name_mapping.get(central_stage)

                        vals_to_write = {}

                        trigger_email_stage = False

                        if target_stage and ticket.stage != target_stage:
                            vals_to_write['stage'] = target_stage
                            if target_stage in ['on_hold', 'delivery']:
                                trigger_email_stage = target_stage

                        if ticket.confirmation_status != central_note:
                            vals_to_write['confirmation_status'] = central_note

                        if ticket.contact_person_name != rep_name:
                            vals_to_write['contact_person_name'] = rep_name

                        if ticket.contact_person_email != rep_email:
                            vals_to_write['contact_person_email'] = rep_email

                        if ticket.contact_person_calendar_link != rep_calendar:
                            vals_to_write['contact_person_calendar_link'] = rep_calendar

                        if vals_to_write:
                            ticket.write(vals_to_write)

                            if trigger_email_stage:
                                ticket._send_customer_stage_email_notification(trigger_email_stage)

            except Exception as e:
                _logger.error(f"Pull cron failed for ticket ID {ticket.id}: {str(e)}")

        return {
            'type': 'ir.actions.client',
            'tag': 'reload'
        }

    def action_book_appointment(self):
        self.ensure_one()

        if not self.contact_person_calendar_link:
            raise ValidationError(_("No appointment link could be found for your company representative!"))

        return {
            'type': 'ir.actions.act_url',
            'url': self.contact_person_calendar_link,
            'target': 'new',
        }