# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger("TeqStars:bol-post-migration-v1.0.3")


def migrate(cr, version):
    """
    Post-migration script to recreate Bol.com scheduled actions using bol_setup_schedule_actions after upgrade.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    bol_instances = env['mk.instance'].search([('marketplace', '=', 'bol')])
    for instance in bol_instances:
        try:
            instance.create_update_schedule_actions()
        except Exception as e:
            _logger.exception("Failed to update Bol.com cron jobs for instance %s: %s", instance.name,e)
    cr.commit()
