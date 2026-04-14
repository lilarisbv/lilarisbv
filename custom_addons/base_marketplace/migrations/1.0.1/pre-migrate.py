# -*- coding: utf-8 -*-

from odoo import api, SUPERUSER_ID
from odoo.tools.sql import column_exists


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if column_exists(env.cr, "mk_log_line", "log_id"):
        cr.execute("""
            DELETE FROM mk_log_line
            WHERE log_id IS NULL
        """)
