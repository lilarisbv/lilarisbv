from odoo import api, fields, models


class MKScopeWizard(models.TransientModel):
    _name = 'marketplace.scope.wizard'
    _description = 'Marketplace API Permissions Confirmation Wizard'

    permissions_html_missing = fields.Html(string="Missing HTML", readonly=True, sanitize=False)
    permissions_html_granted = fields.Html(string="Granted HTML", readonly=True, sanitize=False)
    mk_instance_name = fields.Char(string='Marketplace Instance name')

    def _get_scopes(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This method returns the set of granted and requested access scopes.
        required_scopes is set of predefined access scope e.g. { 'write_customers','write_discounts','write_gift_cards','write_inventory','write_locations'}
        granted_scopes is set of granted scope in marketplace app e.g. {'write_customers','write_discounts','write_gift_cards'}
        missing contains e.g. {'write_inventory','write_locations'}
        granted contains e.g. {'write_customers','write_discounts','write_gift_cards'}
        Returns:
            missing (set): set of missing scopes.
            granted (set): set of granted scopes.
        """
        mk_instance_id = self.env['mk.instance'].browse(self.env.context.get('mk_instance_id'))

        granted_scopes = mk_instance_id._get_granted_access_scopes()
        required_scopes = mk_instance_id._get_required_access_scopes()

        missing = required_scopes - granted_scopes
        granted = required_scopes - missing

        return missing, granted

    def make_chips(self, scopes, cls, icon):
        """
        Task: T4565 - Marketplace Access Rights Check
        Builds <div class="scope-chip …"><i class="fa …"></i>Scope Name</div>
        """
        html = ""
        for scope in scopes:
            html += (
                f'<div class="scope-chip {cls}">'
                f'  <i class="fa {icon}" aria-hidden="true"></i>'
                f'  <span>{scope}</span>'
                f'</div>'
            )
        return html

    @api.model
    def default_get(self, fields_list):
        """
        Task: T4565 - Marketplace Access Rights Check
        This method set default value for 'permissions_html_missing' and 'permissions_html_granted' field and also applying the style.
        Returns:
            res (dictionary): dictionary that sets default value for the field.
        """
        res = super().default_get(fields_list)
        missing, granted = self._get_scopes()
        res['permissions_html_missing'] = self.make_chips(missing, 'missing', 'fa-times-circle')
        res['permissions_html_granted'] = self.make_chips(granted, 'granted', 'fa-check-circle')
        return res

    def action_ignore_and_confirm(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        Ignore the missing scopes and establishes a connection to the marketplace, sets the pricelist, and imports location from marketplace.
        """
        mk_instance_id = self.env['mk.instance'].browse(self.env.context.get('mk_instance_id'))
        mk_instance_id.confirm_connection()

    def action_check_again(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This method recheck the marketplace access scopes.
        Returns:
            action (dictionary): Reload the permission wizard.
        """

        # Get set of granted and requested access scopes.
        missing, granted = self._get_scopes()

        # Based on rechecking reset granted and requested access scopes.
        self.permissions_html_missing = self.make_chips(missing, 'missing', 'fa-times-circle')
        self.permissions_html_granted = self.make_chips(granted, 'granted', 'fa-check-circle')

        return {
            'name': 'Confirm Permissions',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'view_mode': 'form',
            'res_model': 'marketplace.scope.wizard',
            'res_id': self.id,
        }
