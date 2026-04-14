# -*- coding: utf-8 -*-

import logging

from odoo import api, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)
_server_action_logger = _logger.getChild("server_action_safe_eval")


class ServerAction(models.Model):
    _inherit = 'ir.actions.server'

    @api.model
    def _run_action_code_multi(self, eval_context):
        module_id = self.env.ref('base.module_base_marketplace', raise_if_not_found=False)
        if module_id:
            downstream_dep = module_id.downstream_dependencies(module_id)
            downstream_dep_name_list = downstream_dep and downstream_dep.mapped('name') or []

            is_exist = self.xml_id.split('.')[0] in downstream_dep_name_list
            if is_exist:
                safe_eval(self.code.strip(), eval_context, mode="exec", filename=str(self))
                return eval_context.get('action')
        res = super()._run_action_code_multi(eval_context)
        return eval_context.get('action', res)
