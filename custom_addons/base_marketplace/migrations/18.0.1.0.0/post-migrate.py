# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID
from odoo.tools.sql import column_exists


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if column_exists(env.cr, "mk_instance", "temp_analytic_id"):
        cr.execute("""UPDATE mk_instance SET analytic_distribution = jsonb_build_object(temp_analytic_id::TEXT, 100) WHERE temp_analytic_id IS NOT NULL;""")
        cr.execute("""ALTER TABLE mk_instance DROP COLUMN temp_analytic_id""")

