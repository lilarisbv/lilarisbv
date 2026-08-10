# -*- coding: utf-8 -*-

from odoo import models, fields, api


class ResUsers(models.Model):
    _inherit = 'res.users'

    show_marketplace_quick_onboarding = fields.Boolean(string='Show Marketplace Quick Onboarding Panel', default=True,
                                                       help='If enabled, the Quick Onboarding panel will be displayed on the marketplace overview screen.')

    @api.model
    def reset_marketplace_onboarding_for_all_users(self):
        """
        Reset the onboarding panel for all users.
        This can be called from an action or button if needed.
        """
        self.search([]).write({
            'show_marketplace_quick_onboarding': True
        })
        return True
