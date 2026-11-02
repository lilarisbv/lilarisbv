import logging
from odoo import models, fields, Command, _
from odoo.tools.misc import format_date
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException

_logger = logging.getLogger("Teqstars: Base Marketplace")


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='restrict', copy=False)
    marketplace = fields.Selection(related="mk_instance_id.marketplace", string='Marketplace')
    mk_id = fields.Char("Marketplace Identification", copy=False)
    mk_order_number = fields.Char("Order Number", copy=False)
    updated_in_marketplace = fields.Boolean("Updated in Marketplace?", copy=False)
    canceled_in_marketplace = fields.Boolean("Cancel in Marketplace", default=False, copy=False)
    order_workflow_id = fields.Many2one("order.workflow.config.ts", "Marketplace Workflow", copy=False)
    stock_moves_count = fields.Integer(compute="_compute_stock_move_count", string="Stock Moves", store=False, help="Stock Move Count for Sale Order without Stock Picking.")

    def get_odoo_tax(self, mk_instance_id, tax_lines, taxes_included):
        """
        Retrieve or create Odoo taxes based on marketplace tax lines.
        Args:
            mk_instance_id: The marketplace instance object.
            tax_lines (list): List of tax lines provided by the marketplace.
            taxes_included (bool): Indicates if taxes are included in the prices.
        Returns:
            list: A list of tax IDs in Odoo format [(6, 0, tax_list)].
        """
        tax_ids = self.env['account.tax']
        # Determine tax price inclusion policy
        tax_included_override = 'tax_included' if taxes_included else 'tax_excluded'

        for tax_line in tax_lines:
            rate = self._get_tax_rate(tax_line, mk_instance_id)

            # Construct the tax title and search for the tax in Odoo
            # Task: T7493 - Converted strings to f-strings.
            tax_title = f"{tax_line['title']} {rate} {'Included' if taxes_included else 'Excluded'}"
            tax_ids |= self._find_or_create_tax(tax_title, rate, tax_included_override, mk_instance_id)

        return tax_ids

    def _get_tax_rate(self, tax_line, mk_instance_id):
        """
        Get the rounded tax rate based on marketplace instance configuration.
        Args:
            tax_line (dict): A single tax line with tax rate information.
            mk_instance_id: The marketplace instance object.
        Returns:
            float: The rounded tax rate.
        """
        # Apply rounding if configured on the instance, else return the original rate
        return round(tax_line['rate'], mk_instance_id.tax_rounding) if mk_instance_id and mk_instance_id.tax_rounding else tax_line['rate']

    def _find_or_create_tax(self, tax_title, rate, tax_included_override, mk_instance_id):
        """
        Find or create a tax in Odoo based on the given parameters.
        Args:
            tax_title (str): The name of the tax.
            rate (float): The tax rate.
            tax_included_override (str): Indicates if the tax is included or excluded.
            mk_instance_id: The marketplace instance object.
        Returns:
            account.tax record: The found or newly created tax record.
        """
        tax_obj = self.env['account.tax']
        company_id = mk_instance_id.company_id

        # Construct the domain conditionally based on the value of tax_included_override
        domain = [
            ('name', '=', tax_title),
            ('amount', '=', rate),
            ('type_tax_use', '=', 'sale'),
            ('company_id', 'parent_of', company_id.id)
        ]

        if tax_included_override == 'tax_excluded':
            domain += [
                '|',
                ('price_include_override', '=', tax_included_override),
                ('price_include_override', '=', False),
                '|',
                ('active', '=', False),
                ('active', '=', True)
            ]
        elif tax_included_override == 'tax_included':
            domain += [
                ('price_include_override', '=', tax_included_override),
                '|',
                ('active', '=', False),
                ('active', '=', True)
            ]

        # Search for an existing tax
        tax_id = tax_obj.search(domain, limit=1)

        # If tax is found but inactive, reactivate it
        if tax_id and not tax_id.active:
            tax_id.sudo().active = True

        # Create new tax if not found
        if not tax_id:
            tax_id = self._create_new_tax(tax_title, rate, tax_included_override, mk_instance_id)

        return tax_id

    def _create_new_tax(self, tax_title, rate, tax_included_override, mk_instance_id):
        """
        Create a new tax in Odoo if it does not already exist.
        Args:
            tax_title (str): The name of the tax.
            rate (float): The tax rate.
            tax_included_override (str): Indicates if the tax is included or excluded.
            mk_instance_id: The marketplace instance object.
        Returns:
            account.tax record: The newly created tax record.
        """
        tax_obj = self.env['account.tax']
        company_id = mk_instance_id.company_id

        mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
        queue_line_id = self.env.context.get('queue_line_id', False)

        # Task: T7493 - Converted strings to f-strings.
        tax_vals = {
            'name': tax_title,
            'amount': rate,
            'type_tax_use': 'sale',
            'price_include_override': tax_included_override,
            'company_id': company_id.id,
            'description': f"{rate}% {'Included' if tax_included_override == 'tax_included' else 'Excluded'}"
        }

        tax_id = tax_obj.sudo().create(tax_vals)

        # Set accounts if defined on the marketplace instance
        self._assign_tax_accounts(tax_id, mk_instance_id)

        # Log the creation of the new tax
        log_message = _("IMPORT ORDER: Tax not found, so created new Tax '%s' for Company '%s' with rate %s%%.") % (tax_title, company_id.name, rate)
        mk_log_line_dict['success'].append({'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False})

        return tax_id.sudo(False)

    def _assign_tax_accounts(self, tax_id, mk_instance_id):
        """
        Assign tax and refund accounts to the newly created tax based on the marketplace instance.

        Args:
            tax_id: The newly created tax record.
            mk_instance_id: The marketplace instance object.
        """
        tax_id = tax_id.sudo()
        # Assign tax account
        if mk_instance_id.tax_account_id:
            tax_repartition_lines = tax_id.invoice_repartition_line_ids.filtered(lambda x: x.repartition_type == 'tax')
            if tax_repartition_lines:
                tax_repartition_lines.account_id = mk_instance_id.tax_account_id.id

        # Assign refund account
        if mk_instance_id.tax_refund_account_id:
            refund_repartition_lines = tax_id.refund_repartition_line_ids.filtered(lambda x: x.repartition_type == 'tax')
            if refund_repartition_lines:
                refund_repartition_lines.account_id = mk_instance_id.tax_refund_account_id.id

    def prepare_sales_order_vals_ts(self, vals, mk_instance_id):
        order_vals = {
            'partner_id': vals.get('partner_id'),
            'partner_invoice_id': vals.get('partner_invoice_id'),
            'partner_shipping_id': vals.get('partner_shipping_id'),
            'warehouse_id': vals.get('warehouse_id'),
            'company_id': vals.get('company_id', self.env.user.company_id.id),
        }

        fiscal_position_id = order_vals.get('fiscal_position_id', (vals.get('fiscal_position_id', False) if mk_instance_id.tax_system != 'according_to_marketplace' else False))

        if vals.get('name', False):
            order_vals.update({'name': vals.get('name', '')})

        order_vals.update({
            'state': 'draft',
            'date_order': vals.get('date_order', ''),
            'company_id': vals.get('company_id'),
            'picking_policy': vals.get('picking_policy'),
            'partner_invoice_id': vals.get('partner_invoice_id'),
            'partner_shipping_id': vals.get('partner_shipping_id'),
            'partner_id': vals.get('partner_id'),
            'client_order_ref': vals.get('client_order_ref', ''),
            'team_id': vals.get('team_id', ''),
            'carrier_id': vals.get('carrier_id', ''),
            'pricelist_id': vals.get('pricelist_id', ''),
            'fiscal_position_id': fiscal_position_id,
            'payment_term_id': vals.get('payment_term_id', ''),
        })
        return order_vals

    def get_mk_listing_item_for_mk_order(self, mk_id, mk_instance_id):
        return False if not mk_id else self.env['mk.listing.item'].search([('mk_instance_id', '=', mk_instance_id.id), ('mk_id', '=', mk_id)])

    def open_sale_order_in_marketplace(self):
        self.ensure_one()
        if hasattr(self, '%s_open_sale_order_in_marketplace' % self.marketplace):
            url = getattr(self, '%s_open_sale_order_in_marketplace' % self.marketplace)()
            if url:
                client_action = {
                    'type': 'ir.actions.act_url',
                    'name': "Marketplace URL",
                    'target': 'new',
                    'url': url,
                }
                return client_action
        marketplace_values = dict(self.mk_instance_id._fields['marketplace'].selection)
        raise MarketplaceException(_("Navigation not available for %s marketplace.") % marketplace_values.get(self.mk_instance_id.marketplace))

    def check_marketplace_order_date(self, order_create_date, mk_instance_id):
        if mk_instance_id.import_order_after_date and str(mk_instance_id.import_order_after_date) > order_create_date:
            return False
        return True

    def _compute_stock_move_count(self):
        # The work of this method is get the number of stock moves link with the order and count it.
        self.stock_moves_count = self.env["stock.move"].search_count([("sale_line_id", "in", self.order_line.ids), ("picking_id", "=", False)])

    def _prepare_invoice(self):
        invoice_vals = super(SaleOrder, self)._prepare_invoice()
        order_workflow_id = self.order_workflow_id.sudo()
        if order_workflow_id:
            if order_workflow_id.sale_journal_id:
                invoice_vals.update({'journal_id': order_workflow_id.sale_journal_id.id})
            if order_workflow_id.force_invoice_date:
                invoice_vals.update({'invoice_date': self.date_order})
            if order_workflow_id.is_force_payment_reference:
                invoice_vals.update({'payment_reference': self.mk_order_number})
        if self.mk_instance_id:
            invoice_vals.update({'mk_instance_id': self.mk_instance_id.id,'fiscal_position_id':self.fiscal_position_id.id})
        return invoice_vals

    def _get_order_fulfillment_status(self):
        # Hook type method that will get fulfillment status according to marketplace type.
        fulfillment_status = self.env.context.get('is_fulfilled_order', False)
        if hasattr(self, '%s_get_order_fulfillment_status' % self.marketplace):
            fulfillment_status = getattr(self, '%s_get_order_fulfillment_status' % self.marketplace)()
        return fulfillment_status

    def _prepare_confirmation_values(self):
        #When confirm order to set marketplace create order date. We do not need to set current date time.
        result = super()._prepare_confirmation_values()
        if self.mk_instance_id and self.env.context.get('create_date'):
            result.update({'date_order': self.env.context.get('create_date')})
        return result

    def _should_be_locked(self):
        # if user have access for the locked order it will automatically locked the order.
        # So checked locked order in the workflow if yes then locked otherwise it is not locked.
        self.ensure_one()
        should_lock = super(SaleOrder, self)._should_be_locked()
        if self.mk_instance_id:
            return should_lock and self.order_workflow_id.sudo().is_lock_order
        else:
            return should_lock

    def _get_product_quantity_and_lot(self):
        """
        Task:T6366 - Hook method to validate stock availability and lot/serial assignment
        based on the marketplace-specific implementation.

        The method dynamically invokes a marketplace-specific handler named
        ``<marketplace>_get_product_quantity_and_lot`` when available.
        This allows each marketplace to define its own stock validation logic
        before order fulfillment is processed.

        Returns:
            tuple(bool, bool):
                - has_stock: True when all required products have sufficient
                  stock available for fulfillment.
                - has_lot_assigned: True when all required lot/serial-tracked
                  products have valid lot/serial information available.
        """

        has_stock, has_lot_assigned = True, True
        if hasattr(self, '%s_get_product_quantity_and_lot' % self.marketplace):
            has_stock, has_lot_assigned = getattr(self, '%s_get_product_quantity_and_lot' % self.marketplace)()
        return has_stock, has_lot_assigned

    def process_order(self, order_workflow_id):
        """
        Task: T6366 - Process the marketplace order according to its fulfillment status
        and inventory validation rules.

        Args:
            order_workflow_id: Workflow configuration that controls order
                processing and locking behavior.

        Returns:
            bool: Always returns True when processing completes successfully.
        """
        self = self.sudo()
        try:
            if self.state not in ['sale']:
                fulfillment_status = self._get_order_fulfillment_status()
                if self.env.context.get('create_date', False):
                    self.write({'date_order': self.env.context.get('create_date')})
                if fulfillment_status:
                    has_stock, has_lot_assigned = self._get_product_quantity_and_lot()
                    if has_stock and has_lot_assigned:
                        self.process_fulfilled_order(fulfillment_status)
                        if fulfillment_status == 'partial':
                            self.action_confirm()
                    else:
                        self.action_confirm()
                else:
                    self.action_confirm()
            if order_workflow_id.is_lock_order and not self.locked:
                self.action_lock()
        except Exception as e:
            raise
        return True

    def _prepare_payment_vals(self, order_workflow_id, invoice_id, amount=0.0):
        payment_vals = {
            'amount': amount or invoice_id.amount_residual,
            'date': invoice_id.date,
            'memo': invoice_id.payment_reference or invoice_id.ref or invoice_id.name,
            'partner_id': invoice_id.commercial_partner_id.id,
            'partner_type': 'customer',
            'currency_id': invoice_id.currency_id.id,
            'journal_id': order_workflow_id.journal_id.id,
            'invoice_ids': [Command.link(invoice_id.id)]
        }
        order_workflow_id.payment_method_line_id and payment_vals.update({'payment_method_line_id': order_workflow_id.payment_method_line_id.id})
        return payment_vals

    def pay_and_reconcile(self, order_workflow_id, invoice_id):
        if not self.env.context.get('manual_validate', False) and hasattr(self, '%s_pay_and_reconcile' % self.mk_instance_id.marketplace):
            return getattr(self, '%s_pay_and_reconcile' % self.mk_instance_id.marketplace)(order_workflow_id, invoice_id)
        payment_vals = self._prepare_payment_vals(order_workflow_id, invoice_id)
        payment = self.env['account.payment'].create(payment_vals)
        payment.action_post()
        liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
        lines = (counterpart_lines + invoice_id.line_ids.filtered(lambda line: line.account_type == 'asset_receivable'))
        source_balance = abs(sum(lines.mapped('amount_residual')))
        payment_balance = abs(sum(counterpart_lines.mapped('balance')))
        delta_balance = source_balance - payment_balance

        # Balance are already the same.
        if not invoice_id.company_currency_id.is_zero(delta_balance):
            lines.reconcile()
        return True

    def process_invoice(self, order_workflow_id):
        """Create, validate, and register payment for invoices based on workflow configuration.

        - Creates invoices if `is_create_invoice` is enabled, order is in 'sale' state, and fiscal lock date allows it.
        - When `is_create_single_invoice` is True on the marketplace instance, skips invoice creation if there are
          undelivered delivery-policy products (waits until all products are ready for a single invoice).
        - Validates (posts) draft invoices if `is_validate_invoice` is enabled.
        - Registers payment for unpaid invoices if `is_register_payment` is enabled.

        Args:
            order_workflow_id: The marketplace workflow configuration record.
        Returns:
            bool: True if the method completes without errors.
        """
        self = self.sudo()
        try:
            if order_workflow_id.is_create_invoice and self.state == 'sale' and self._check_fiscalyear_lock_date():
                if not self.invoice_ids and not (self.mk_instance_id.is_create_single_invoice and self.order_line.filtered(
                        lambda x: x.product_id.invoice_policy == 'delivery' and x.qty_delivered < x.product_uom_qty)):
                    self._create_invoices()
                if order_workflow_id.is_validate_invoice:
                    for invoice_id in self.invoice_ids.filtered(lambda i: i.payment_state == 'not_paid' and i.state in ['draft', 'posted']):
                        if invoice_id.state == 'draft':
                            invoice_id.action_post()
                        if order_workflow_id.is_register_payment:
                            self.pay_and_reconcile(order_workflow_id, invoice_id)
        except Exception as e:
            raise
        return True

    def _check_fiscalyear_lock_date(self):
        lock_date = self.company_id._get_user_fiscal_lock_date(self.order_workflow_id.sale_journal_id)
        if self.date_order.date() <= lock_date:
            mk_log_id = self.env.context.get('mk_log_id', False)
            log_message = _("PROCESS ORDER: You cannot create invoice prior to and inclusive of the lock date %s for Marketplace Order %s.") % (format_date(self.env, lock_date), self.name)
            if mk_log_id:
                mk_log_id = self.env.context.get('mk_log_id', False)
                queue_line_id = self.env.context.get('queue_line_id', False)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            _logger.error(log_message)
            return False
        return True

    def do_marketplace_workflow_process(self, marketplace_workflow_id=False, order_list=None):
        """Execute the marketplace workflow for one or more sale orders.

        Processes each order in two phases:
        1. **Order Confirmation** (if `is_confirm_order` is enabled): Confirms the order,
           marks service products as delivered, and skips invoicing if only delivery-policy
           storable products exist (awaiting delivery).
        2. **Invoice Processing**: Calls `process_invoice` to create, validate, and register
           payment for invoices based on workflow settings.

        Already fully invoiced orders are skipped.

        Args:
            marketplace_workflow_id: Fallback workflow configuration if an order has none assigned.
            order_list: List of sale order records to process. Defaults to `[self]`.
        Returns:
            bool: True if all orders are processed successfully.
        """
        if order_list is None or not order_list:
            order_list = [self]
        if not order_list:
            return False
        for order_id in order_list:
            order_workflow_id = order_id.order_workflow_id
            if not order_workflow_id:
                order_workflow_id = marketplace_workflow_id
            if order_id.invoice_status and order_id.invoice_status == 'invoiced':
                continue

            # Process Sale Order
            if order_workflow_id.is_confirm_order:
                if not order_id.process_order(order_workflow_id):
                    continue

                self._mark_service_products_delivered(order_id)

                order_line_ids = order_id.order_line.filtered(lambda x: x.product_id.invoice_policy == 'order')
                if not order_line_ids.filtered(lambda x: x.is_storable) and len(order_id.order_line) != len(order_line_ids.filtered(lambda y: y.product_id.type in ['service', 'consu'])):
                    continue

            # Process Invoice
            order_id.process_invoice(order_workflow_id)
        return True

    def _mark_service_products_delivered(self, order_id):
        """Marks service products with 'delivered_manual' policy as fully delivered.
        Args:
            order_id: The sales order to process.
        Returns:
            True if the module 'sale_project' is installed, otherwise False.
        """

        sale_project_installed = self.mk_instance_id._is_module_installed('sale_project')

        if sale_project_installed:
            order_lines = order_id.order_line.filtered(lambda x: x.product_id.service_policy == 'delivered_manual' and x.product_id.type == 'service')

            for order_line in order_lines:
                order_line.qty_delivered = order_line.product_uom_qty
            return True

        return False

    def process_fulfilled_order(self, fulfillment_status=True):
        """
        Processes the fulfillment of orders based on the status ('partial' or 'fulfilled').
        Args:
            fulfillment_status (bool): Indicates if the order is fulfilled ('fulfilled') or partially fulfilled ('partial').
        Returns:
            bool: True if the order is successfully processed.
        """
        # Pre-fetch necessary objects
        location_dest_id = self._get_location('customer')
        location_id = self._get_location('supplier')

        # Filter out service products early to avoid unnecessary processing
        sale_lines = self.order_line.filtered(lambda l: l.product_id.type != 'service')

        # Prepare fulfillment dictionary for 'partial' fulfillment if applicable
        fulfillment_dict = self._prepare_fulfillment_dict(sale_lines, fulfillment_status)

        # Process stock moves for each sale order line
        for sale_line, quantity in fulfillment_dict.items():
            bom_lines = self._check_bom_if_applicable(sale_line.product_id)

            # Process stock moves based on BOM and dropshipping
            self._process_stock_moves(sale_line, quantity, location_dest_id, location_id, bom_lines)

        # Update order state and marketplace status
        self.write({'state': 'sale', 'updated_in_marketplace': fulfillment_status})

        return True

    def _prepare_fulfillment_dict(self, sale_lines, fulfillment_status):
        """
        Prepare the fulfillment dictionary based on the status.
        Args:
            sale_lines: The sale lines to be processed.
            fulfillment_status: The fulfillment status ('fulfilled' or 'partial').
        Returns:
            dict: A dictionary with sale order lines as keys and quantities as values.
        """
        fulfillment_dict = {line: line.product_uom_qty for line in sale_lines}

        if fulfillment_status == 'partial':
            # Call a hook method to allow partial fulfillment
            hook_method = '%s_prepare_partial_fulfillment_value' % self.marketplace
            if hasattr(self, hook_method):
                fulfillment_dict = getattr(self, hook_method)(sale_lines)

        return fulfillment_dict

    def _process_stock_moves(self, sale_line, quantity, location_dest_id, location_id, bom_lines):
        """
        Processes stock moves for a given sale line based on BOM or dropshipping.
        Args:
            sale_line: The sale order line being processed.
            quantity: The quantity to be processed.
            location_dest_id: The destination location.
            location_id: The supplier location (if applicable).
            bom_lines: List of BOM lines for the product (if any).
        """
        if bom_lines:
            for bom_line in bom_lines:
                self._create_and_done_stock_move(sale_line, quantity, location_dest_id, bom_line=bom_line)
        elif self.check_product_dropship(sale_line.product_id):
            self._create_and_done_stock_move(sale_line, quantity, location_dest_id, location_id=location_id)
        else:
            self._create_and_done_stock_move(sale_line, quantity, location_dest_id)

    def _check_bom_if_applicable(self, product_id):
        """
        Checks for BOM components if MRP is installed and applicable for the product.
        Args:
            product_id: The product being checked.
        Returns:
            list: BOM lines if applicable, empty list otherwise.
        """
        if self.mk_instance_id._is_module_installed('mrp'):
            return self.check_bom_product_ts(product_id)
        return []

    def _create_and_done_stock_move(self, sale_line, quantity, location_dest_id, location_id=False, bom_line=False):
        """
        Create and process a stock move for a sale order line.
        Args:
            sale_line: The sale order line being processed.
            quantity: The quantity to be processed.
            location_dest_id: The destination location for the stock move.
            location_id: The supplier location (if applicable).
            bom_line: The BOM line data (if applicable).
        Returns:
            The created stock move object.
        """
        # Determine product and quantity details
        if bom_line:
            product_id = bom_line[0].product_id
            quantity = bom_line[1].get('qty', 0) * quantity
            product_uom_id = bom_line[0].product_uom_id
        else:
            product_id = sale_line.product_id
            product_uom_id = sale_line.product_uom_id

        # Create and complete the stock move
        if product_id and quantity and product_uom_id:
            move_vals = self._get_move_raw_values_ts(product_id, quantity, product_uom_id, location_id, location_dest_id, sale_line, bom_line)
            move_id = self.env['stock.move'].create(move_vals)
            move_id._action_assign(force_qty=quantity)
            move_id._set_quantity_done(quantity)
            move_id._action_done()
            return move_id

    def _get_location(self, usage_type):
        """
        Fetch a stock location based on the usage type and company.
        Args:
            usage_type: The usage type of the location ('customer' or 'supplier').
        Returns:
            The location object found for the given usage type.
        """
        return self.env['stock.location'].search(['|', ('company_id', '=', self.company_id.id), ('company_id', '=', False), ('usage', '=', usage_type)], limit=1)

    def create_stock_move_and_done_ts(self, sale_line, quantity, location_dest_id, location_id=False, bom_line=False):
        # The work of this method is creating stock moves and done it based on order line.
        if bom_line:
            product_id = bom_line[0].product_id
            quantity = bom_line[1].get('qty', 0) * quantity
            product_uom_id = bom_line[0].product_uom_id
        else:
            product_id = sale_line.product_id
            product_uom_id = sale_line.product_uom
        if product_id and quantity and product_uom_id:
            move_vals = self._get_move_raw_values_ts(product_id, quantity, product_uom_id, location_id, location_dest_id, sale_line, bom_line)
            move_id = self.env['stock.move'].create(move_vals)
            move_id._action_assign(force_qty=quantity)
            move_id._set_quantity_done(quantity)
            move_id._action_done()
            return move_id

    def _get_move_raw_values_ts(self, product, quantity, product_uom, location_id, location_dest_id, order_line, bom_line):
        # The work of this method is preparing the values of stock move.
        vals = {
            'origin': self.name,
            'product_id': product.id if product else False,
            'product_uom_qty': quantity,
            'product_uom': product_uom.id if product_uom else False,
            'location_dest_id': location_dest_id.id,
            'location_id': location_id.id if location_id else self.warehouse_id.lot_stock_id.id,
            'company_id': self.company_id.id,
            'state': 'confirmed',
            'sale_line_id': order_line.id,
            'picked': True,
        }
        if bom_line:
            vals.update({'bom_line_id': bom_line[0].id})
        return vals

    def action_view_stock_moves_ts(self):
        # The work of this action is get the all stock moves which is link with the sale order.
        stock_move_obj = self.env['stock.move']
        move_ids = stock_move_obj.search([('picking_id', '=', False), ('sale_line_id', 'in', self.order_line.ids)]).ids
        action = {
            'name': 'Sale Order Stock Move',
            'res_model': 'stock.move',
            'type': 'ir.actions.act_window',
            'view_mode': 'list,form',
            'domain': [('id', 'in', move_ids)],
        }
        return action

    def check_product_dropship(self, product_id):
        # The work of this method is checking the product is drop ship or not.
        location_dest_ids = self.env['stock.location'].search(['|', ('company_id', '=', self.company_id.id), ('company_id', '=', False), ('usage', '=', 'customer')])
        route_ids = product_id.route_ids or product_id.categ_id.route_ids
        stock_rule = self.env['stock.rule'].search(
            [('company_id', '=', self.company_id.id), ('action', '=', 'buy'), ('location_dest_id', 'in', location_dest_ids.ids), ('route_id', 'in', route_ids.ids)])
        if stock_rule:
            return True
        return False

    def check_bom_product_ts(self, product_id):
        # The work of this method is checking the bom product and return its components.
        try:
            bom = self.env['mrp.bom'].sudo()._bom_find(product_id, company_id=self.company_id.id, bom_type='phantom')[product_id]
            if bom:
                factor = product_id.uom_id._compute_quantity(1, bom.product_uom_id) / bom.product_qty
                boms, lines = bom.sudo().explode(product_id, factor, picking_type=bom.picking_type_id)
                return lines
        except Exception as e:
            # Task: T7493 - Converted strings to f-strings.
            _logger.info(f"Error when trying to find BOM product components for Order {self.name}. ERROR: {e}")
        return {}

    def _check_invoice_policy_ordered(self):
        # If any of the order line has invoice policy delivery and ordered qty is not delivered the return False.
        order_lines = self.order_line.filtered(lambda x: x.product_id.invoice_policy == 'delivery' and x.product_id.type != 'service')
        for order_line in order_lines:
            if not order_line.product_uom_qty == order_line.qty_delivered:
                return False
        return True

    def _action_cancel(self):
        res = super(SaleOrder, self.sudo())._action_cancel()
        # While cancel the order, revert the stock move.
        for sale_order in self:
            if sale_order.stock_moves_count:
                return_picking_obj = self.env['stock.return.picking'].sudo()
                return_wizard_vals = return_picking_obj.with_context(active_model='sale.order', active_id=sale_order.id).default_get([])
                return_wizard_id = return_picking_obj.create(return_wizard_vals)
                return_wizard_id.with_context(skip_error=True).action_create_returns_ts()
        return res

    def _get_existing_order(self, mk_id, mk_instance_id):
        """Retrieve an existing Odoo order by Marketplace ID and marketplace instance."""
        return self.sudo().search([('mk_id', '=', mk_id), ('mk_instance_id', '=', mk_instance_id.id)])


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    mk_id = fields.Char("Marketplace Identification", copy=False)
    is_discount = fields.Boolean("Is Marketplace Discount", copy=False)
    related_disc_sale_line_id = fields.Many2one("sale.order.line", copy=False)
    marketplace_discount_amount = fields.Float("Marketplace Discount Amount", copy=False)

    def prepare_sale_order_line_ts(self, vals, mk_instance):
        order_line = {
            'name': vals.get('description'),
            'order_id': vals.get('order_id'),
            'product_id': vals.get('product_id', ''),
            'product_uom_id': vals.get('product_uom'),
            'company_id': vals.get('company_id', ''),
            'state': 'draft',
            'product_uom_qty': vals.get('order_qty', 0.0),
            'price_unit': vals.get('price_unit', 0.0),
            'discount': vals.get('discount', 0.0),
        }
        if mk_instance:
            order_line.update({'analytic_distribution': mk_instance.analytic_distribution})
        return order_line

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        """ inherit method to pass marketplace_discount_amount. It will be used while calculate unit price.
        :return: A python dictionary.
        """
        res = super()._prepare_base_line_for_taxes_computation(**kwargs)
        quantity = res.get('quantity', 0)
        if self.marketplace_discount_amount and quantity:
            res['marketplace_discount_amount'] = self.marketplace_discount_amount / quantity
        return res
