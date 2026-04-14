import logging

from dateutil.relativedelta import relativedelta

from odoo import fields, models, api, tools, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from .misc import convert_bol_datetime_to_utc

_logger = logging.getLogger("Teqstars:bol")

INVOICE_STATUS_TRANSITION = [('INVOICE_REQUESTED', 'INVOICE REQUESTED'), ('INVOICE_UPLOADED', 'INVOICE UPLOADED'), ('INVOICE_INCORRECT', 'INVOICE INCORRECT'),
                             ('INVOICE_VIRUS_DETECTED', 'INVOICE VIRUS DETECTED'), ('FINISHED', 'FINISHED')]


class BolInvoiceRequest(models.Model):
    _name = 'bol.invoice.request'
    _description = "Bol Invoice Request"
    _order = 'request_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    shipment_id = fields.Char(string="Shipment ID", index=True, help="The BOL shipment identifier associated with this invoice request.")
    order_id = fields.Char(string="Order Name", index=True, help="The reference name of the order related to this invoice request.")
    last_update_date = fields.Datetime(string="Last Update Date", help="The date and time when this invoice request was created or last updated from BOL.")
    sale_order_id = fields.Many2one("sale.order", string="Order")
    invoice_count = fields.Integer(string="Invoice Count", compute="_compute_invoice_count")
    order_count = fields.Integer(string="Order Count", compute="_compute_order_count")
    no_of_retry_count = fields.Integer(string="Retry Count", help="No of count that is use for the check the invoice uploaded count.", )
    show_manual_button = fields.Boolean(compute="_compute_show_manual_button", string="Show Manual Upload",
                                        help="Controls the visibility of the manual invoice upload button. The button is shown only when all required conditions are met.")
    partner_id = fields.Many2one(related="sale_order_id.partner_id", string="Customer", store=True, help="The customer associated with the linked sales order.")
    transitions_line_ids = fields.One2many('bol.invoice.status.transitions.line', 'request_id', string="Status Transitions",
                                           help="A history of status changes and processing events for this invoice request.")
    status = fields.Selection(INVOICE_STATUS_TRANSITION, string="Status", compute="_compute_status", store=True, inverse="_inverse_status", help="The current processing status of the invoice request.")
    request_date = fields.Datetime(string='Date', compute="_compute_request_date", store=True, index=True, help="The date and time when the invoice request was sent to BOL.")
    invoice_ids = fields.Many2many('account.move', compute='_compute_invoice_ids', string='Invoices', store=False,
                                   help="Indicates whether the invoice date lies in the future, which may restrict processing.")
    mk_instance_id = fields.Many2one('mk.instance', string="Instance", ondelete='cascade', help="The marketplace instance used to communicate with BOL for this invoice request.")
    bol_process_status_id = fields.Many2one('bol.process.status', string="Process Status", help="Technical field to identify the process status for request and later to identify process went finished or failed.")

    @api.depends('invoice_ids.invoice_date', 'mk_instance_id.bol_export_invoice_delay', 'mk_instance_id.bol_export_invoice', 'status')
    def _compute_show_manual_button(self):
        today = fields.Date.today()
        for record in self:
            show = True
            if not record.mk_instance_id.bol_export_invoice:
                show = False
            if not record.invoice_ids:
                show = False
            if record.status != 'INVOICE_REQUESTED':
                show = False
            if record.bol_process_status_id and record.bol_process_status_id.state == 'pending':
                show = False
            if show and record.mk_instance_id.bol_export_invoice_delay:
                delay_days = record.mk_instance_id.bol_export_invoice_delay
                min_allowed_date = today - relativedelta(days=delay_days)
                for inv in record.invoice_ids:
                    if not inv.invoice_date or inv.invoice_date >= min_allowed_date:
                        show = False
                        break
            record.show_manual_button = show

    @api.depends("transitions_line_ids.date")
    def _compute_request_date(self):
        for record in self:
            dates = record.transitions_line_ids.mapped("date")
            record.request_date = min(dates) if dates else False

    @api.depends('transitions_line_ids.status', 'transitions_line_ids.date')
    def _compute_status(self):
        for rec in self:
            if rec.transitions_line_ids:
                latest_line = rec.transitions_line_ids.sorted(key=lambda l: l.date or fields.Datetime.min, reverse=True)[0]
                rec.status = latest_line.status
            else:
                rec.status = 'INVOICE_REQUESTED'

    def _inverse_status(self):
        transitions_line = self.env['bol.invoice.status.transitions.line']
        for rec in self:
            if rec.transitions_line_ids and rec.status:
                transitions_line.create({
                    'request_id': rec.id,
                    'status': rec.status,
                    'date': fields.Datetime.now(),
                })

    @api.depends('sale_order_id')
    def _compute_invoice_ids(self):
        for rec in self:
            rec.invoice_ids = rec.sale_order_id.invoice_ids

    @api.depends('shipment_id', 'order_id')
    def _compute_display_name(self):
        for record in self:
            record.display_name = '{} ({})'.format(record.order_id, record.shipment_id)

    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)

    def _compute_order_count(self):
        for rec in self:
            rec.order_count = len(rec.sale_order_id)

    def action_view_invoices(self):
        self.ensure_one()
        invoices = self.invoice_ids
        action = self.env.ref("account.action_move_out_invoice_type").read()[0]
        if len(invoices) == 1:
            action.update({"views": [(self.env.ref("account.view_move_form").id, "form")], "res_id": invoices.id})
        else:
            action["domain"] = [("id", "in", invoices.ids)]
        return action

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            "name": "Sale Order",
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "target": "current",
        }

    def process_manually(self):
        try:
            ir_cron_sudo = self.env['ir.cron'].sudo().with_context(active_test=False)
            cron = ir_cron_sudo.search([('code', '=', f"model.cron_auto_export_bol_invoice({self.sale_order_id.mk_instance_id.id})")])
            self.env['mk.operation']._check_process_cron_job(cron)
        except Exception:
            raise MarketplaceException("UPLOAD INVOICE: Restricted! The processing of upload invoice to Bol is already running via scheduled action. Proceeding with this action may result in duplicate records.")
        self._process_invoice_request(manual_process=True)
        return True

    def action_view_retry_logs(self):
        return True

    def cron_auto_export_bol_invoice(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed' and mk_instance_id.bol_export_invoice:
            self.export_pending_invoice_requests(mk_instance_id)
        return True

    def cron_auto_import_invoice_request(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed' and mk_instance_id.bol_export_invoice:
            self.get_open_import_request_for_invoice(mk_instance_id)
        return True

    def sync_invoice_request_status(self):
        self.ensure_one()
        date_now = fields.Datetime.now()
        response = self.mk_instance_id.with_context(skip_raise=True)._send_bol_request('retailer/shipments/invoices/requests', {}, params={'shipment-id': self.shipment_id})
        if response is True:
            return False
        invoice_requests = response.get('invoiceRequests') or []
        if not invoice_requests:
            return False
        for payload in invoice_requests:
            status_transition_list = payload.get('statusTransitions', []) or []
            self._sync_status_transitions(status_transition_list)
        if self.status != 'INVOICE_REQUESTED':
            self.bol_process_status_id = False
        self.last_update_date = date_now
        return True

    def _sync_open_invoice_requests_status(self, mk_instance_id):
        invoice_requests = self.search([('status', '!=', 'FINISHED'), ('mk_instance_id', '=', mk_instance_id.id)])
        for record in invoice_requests:
            record.sync_invoice_request_status()
        return True

    def get_open_import_request_for_invoice(self, mk_instance_id):
        date_now = fields.Datetime.now()
        params = {'state': 'OPEN'}  # 'ALL'
        response = mk_instance_id._send_bol_request('retailer/shipments/invoices/requests', {}, params=params)
        invoice_requests = response.get('invoiceRequests') or []
        for invoice_request in invoice_requests:
            order_id = invoice_request.get('orderId')
            shipment_id = invoice_request.get('shipmentId')
            if not order_id:
                continue
            sale_order = self._find_sale_order(order_id)
            status_transition_list = invoice_request.get('statusTransitions', []) or []
            bol_invoice_req = self.search([('order_id', '=', order_id), ('shipment_id', '=', shipment_id)], limit=1)
            if not bol_invoice_req:
                bol_invoice_req = self.create({
                    'order_id': order_id,
                    'shipment_id': shipment_id,
                    'last_update_date': date_now,
                    'sale_order_id': sale_order.id,
                    'mk_instance_id': mk_instance_id.id,
                })
            else:
                bol_invoice_req.write({'last_update_date': date_now})
            bol_invoice_req._sync_status_transitions(status_transition_list)
        self._sync_open_invoice_requests_status(mk_instance_id)
        return True

    def _find_sale_order(self, order_id):
        sale_order = self.env['sale.order'].search([('mk_id', '=', order_id)], limit=1)
        return sale_order

    def _sync_status_transitions(self, transitions):
        self.ensure_one()
        transitions_line = self.env['bol.invoice.status.transitions.line']
        for transition in transitions or []:
            status = transition.get('status')
            date = transition.get('statusDateTime')
            if not status or not date:
                continue
            transaction_date = convert_bol_datetime_to_utc(date)
            exists = transitions_line.search([('request_id', '=', self.id), ('status', '=', status)], limit=1)
            if not exists:
                transitions_line.create({'request_id': self.id, 'status': status, 'date': transaction_date})
        return True

    def _raise_or_log_invoice_export_error(self, message, manual_process, prefix="UPLOAD INVOICE"):
        mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        msg = f"{prefix}: {message}"
        if manual_process:
            raise MarketplaceException(_(msg))
        if self.id and not manual_process:
            self.no_of_retry_count = (self.no_of_retry_count or 0) + 1
        mk_log_line_dict['error'].append({'log_message': _(msg)})
        return False

    def export_pending_invoice_requests(self, mk_instance_id):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
        mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        target_statuses = ['INVOICE_REQUESTED', 'INVOICE_INCORRECT', 'INVOICE_VIRUS_DETECTED']
        # 1) First: reconcile existing uploads (pending / finished)
        pending_requests = self.search([('status', 'in', target_statuses),
                                        ('mk_instance_id', '=', mk_instance_id.id), ('bol_process_status_id', '!=', False),
                                        ('bol_process_status_id.state', '=', 'pending')])
        for request in pending_requests:
            try:
                request.sync_invoice_request_status()
                if request.status != 'INVOICE_REQUESTED':
                    request.bol_process_status_id = False
            except Exception as e:
                mk_log_line_dict['error'].append(
                    {'log_message': _('UPLOAD INVOICE: Failed to retrieve invoice request status from Bol.com for Sales Order "{}" (Shipment ID: "{}").'
                                      '\n\nError: {}'.format(self.sale_order_id.name or self.order_id, self.shipment_id, str(e)))})
                continue
        # 2) Upload only records that are eligible
        invoice_requests = self.search([('status', 'in', target_statuses), ('mk_instance_id', '=', mk_instance_id.id),
                                        '|', ('no_of_retry_count', '<', 3), ('no_of_retry_count', '=', False), ('bol_process_status_id', '=', False)])
        invoice_requests.with_context(mk_log_line_dict=mk_log_line_dict, mk_log_id=mk_log_id)._process_invoice_request(manual_process=False)
        self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        return True

    def _process_invoice_request(self, manual_process=False):
        """
        Common logic used by cron and manual button to upload invoice to Bol
        """
        for instance, requests in tools.groupby(self, lambda inv_request: inv_request.mk_instance_id):
            report_id = instance.bol_invoice_report_id
            if not report_id:
                self._raise_or_log_invoice_export_error(
                    'Missing Invoice Report configuration for instance "{}". Configure it in Instance -> Orders: enable "Export Invoices to Bol.com" and select an "Invoice Report"'.format(
                        instance.name), manual_process=manual_process)
                continue
            requests = self.env['bol.invoice.request'].concat(*requests)
            for request in requests:
                order = request.sale_order_id
                if not order:
                    order = self._find_sale_order(request.order_id)
                    if not order:
                        request._raise_or_log_invoice_export_error(
                            'Sales Order not found for invoice request (Order ID: {}, Instance: {})'.format(request.order_id, instance.name),
                            manual_process=manual_process)
                        continue
                    request.write({'sale_order_id': order.id})
                if order.invoice_status != 'invoiced':
                    request._raise_or_log_invoice_export_error(
                        'Sales order {} (Shipment ID: {}) is not fully invoiced yet. Please ensure the order is fully invoiced in Odoo before uploading the invoice to Bol.com.'.format(
                            request.order_id, request.shipment_id), manual_process=manual_process)
                    continue
                request._generate_and_upload_invoice_to_bol(manual_process=manual_process)
                self._cr.commit()
        return True

    def _generate_and_upload_invoice_to_bol(self, manual_process=False):
        """
        Generate invoice PDF and upload it to Bol.com (single entry point).
        - Increments no_of_retry_count on every failure (including PDF generation)
        - Skips cron attempts when retry >= 3 (manual can still try)
        - Provides specific failure messages (PDF / Upload / Status)
        """
        self.ensure_one()
        instance = self.mk_instance_id
        report = self.mk_instance_id.bol_invoice_report_id

        # Skip automatic retries after limit (cron only)
        if not manual_process and (self.no_of_retry_count or 0) >= 3:
            return False

        invoices = self.sale_order_id.invoice_ids.filtered(lambda inv: inv.state == 'posted' and inv.invoice_date)
        if not invoices:
            return False

        # Step 1: Generate PDF
        try:
            pdf_bytes, _ = self.env['ir.actions.report']._render(report, invoices.ids)
            if not pdf_bytes:
                self._raise_or_log_invoice_export_error(
                    'Invoice PDF generation returned empty content for Sales Order "{}" (Shipment ID: "{}").'
                    '\n\nReport: "{}"'.format(self.sale_order_id.name or self.order_id, self.shipment_id, report.display_name),
                    manual_process=manual_process
                )
                return False
        except Exception as e:
            self._raise_or_log_invoice_export_error(
                'PDF generation failed for Sales Order "{}" (Shipment ID: "{}").'
                '\n\nReport: "{}" \nError: {}'.format(self.sale_order_id.name or self.order_id, self.shipment_id, report.display_name, str(e)),
                manual_process=manual_process
            )
            return False

        # Step 2: Upload PDF
        try:
            files = {'invoice': ('invoice.pdf', pdf_bytes, 'application/pdf')}
            response = instance.with_context(force_content_type_for_invoice=True)._send_bol_request(f'/retailer/shipments/invoices/{self.shipment_id}', {}, files=files,
                                                                                                    method='POST')
        except Exception as e:
            self._raise_or_log_invoice_export_error(
                'Invoice upload to Bol.com failed for Sales Order "{}" (Shipment ID: "{}").'
                '\n\nError: {}'.format(self.sale_order_id.name or self.order_id, self.shipment_id, str(e)),
                manual_process=manual_process
            )
            return False

        # Step 3: Create process + check status
        try:
            process = self.env["bol.process.status"].create_or_update_process_status(response, instance)
            self.bol_process_status_id = process.id
            # commented below code to avoid getting process status run time because bol.com taking almost 3+ minutes to complete upload.
            # while True:
            #     process.get_process_status()
            #     if process.state == 'success':
            #         mk_log_line_dict['success'].append({'log_message': 'UPLOAD INVOICE: Successfully upload invoice to Bol.com for Sales Order "{}" (Shipment ID: "{}").'.format(
            #             self.sale_order_id.name or self.order_id, self.shipment_id)})
            #         self.sync_invoice_request_status()
            #         break
            #     if process.state in ['failure', 'timeout']:
            #         self._raise_or_log_invoice_export_error(
            #             'Failed to upload invoice to Bol.com for Sales Order "{}" (Shipment ID: "{}").'
            #             '\n\nProcess Status: {}\n\nError: {}'.format(self.sale_order_id.name or self.order_id, self.shipment_id, process.state.upper(), process.error_message),
            #             manual_process=manual_process
            #         )
            #         return False
        except Exception as e:
            self._raise_or_log_invoice_export_error(
                'Failed to create upload status from Bol.com for Sales Order "{}" (Shipment ID: "{}").'
                '\n\nError: {}'.format(self.sale_order_id.name or self.order_id, self.shipment_id, str(e)),
                manual_process=manual_process
            )
            return False
        return True
