# -*- coding: utf-8 -*-
"""
Apply the Marketplace access-rights change to existing databases.
"""

import logging

from odoo import api, Command, SUPERUSER_ID

_logger = logging.getLogger("TeqStars:Base Marketplace")

LINK = ('sales_team.group_sale_salesman',)
UNLINK = ('sales_team.group_sale_manager', 'account.group_account_invoice',)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        group = env.ref('base_marketplace.group_base_marketplace', raise_if_not_found=False)
        if not group:
            _logger.warning("User group not found. Migration skipped.")
            return

        commands = []
        for xmlid in LINK:
            implied = env.ref(xmlid, raise_if_not_found=False)
            if implied and implied not in group.implied_ids:
                commands.append(Command.link(implied.id))
        for xmlid in UNLINK:
            implied = env.ref(xmlid, raise_if_not_found=False)
            if implied and implied in group.implied_ids:
                commands.append(Command.unlink(implied.id))

        if commands:
            group.write({'implied_ids': commands})

        _logger.info("Updated User group access rights successfully.")
    except Exception as error:
        _logger.error("Failed to update User group access rights: %s", error)
