from odoo import models, fields, api


class CustomerTicketWizard(models.TransientModel):
    _name = 'customer.ticket.wizard'
    _description = 'Ticket Action Wizard'

    ticket_id = fields.Many2one('customer.ticket', string='Ticket', required=True)
    action_type = fields.Selection([
        ('change', 'Request Change'),
        ('cancel', 'Cancel')
    ], string='Action Type', required=True)

    instruction = fields.Char(string='Instruction', compute='_compute_instruction')
    reason = fields.Text(string='Details / Reason', required=True)

    @api.depends('action_type')
    def _compute_instruction(self):
        for rec in self:
            if rec.action_type == 'change':
                rec.instruction = 'Please enter your request details below:'
            else:
                rec.instruction = 'Please briefly explain the reason for cancellation below:'

    def action_submit(self):
        self.ensure_one()
        current_date = fields.Datetime.context_timestamp(self, fields.Datetime.now()).strftime('%Y-%m-%d %H:%M')

        if self.action_type == 'change':
            formatted_reason = f"{self.ticket_id.description or ''}<br/><hr/><strong>[{current_date} - Revision Requested]:</strong> {self.reason}"

            self.ticket_id.write({
                'stage': 'change',
                'description': formatted_reason
            })
            self.ticket_id._call_central_api_respond('reject', f"{self.reason} [{current_date}]")
        elif self.action_type == 'cancel':

            formatted_reason = f"{self.ticket_id.description or ''}<br/><hr/><strong>[{current_date} - Cancellation]:</strong> {self.reason}"

            # Update local ticket stage and description
            self.ticket_id.write({
                'stage': 'cancel',
                'description': formatted_reason
            })

            self.ticket_id._call_central_api_respond('cancel', f"{self.reason} [{current_date}]")
        return {'type': 'ir.actions.act_window_close'}