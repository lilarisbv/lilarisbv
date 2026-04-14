# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round

from odoo import _, api, fields, models


class ReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'
    _description = 'Return Picking'

    sale_order_id = fields.Many2one('sale.order', string='Sales Order')

    def _prepare_stock_return_picking_line_vals_from_move_ts(self, stock_move):
        """
        Prepare values for stock return picking line from a given stock move.
        """
        # Calculate the initial quantity to be returned from the stock move
        quantity = stock_move.quantity
        # Adjust the quantity by subtracting the quantities of destination moves that have been returned
        for move in stock_move.move_dest_ids:
            if not move.origin_returned_move_id or move.origin_returned_move_id != stock_move:
                continue
            quantity -= move.quantity
        # Round the quantity to match the product's unit of measure rounding precision
        quantity = float_round(quantity, precision_rounding=stock_move.product_id.uom_id.rounding)
        return {
            'product_id': stock_move.product_id.id,
            'quantity': quantity,
            'move_id': stock_move.id,
            'uom_id': stock_move.product_id.uom_id.id,
        }

    @api.model
    def default_get(self, fields):
        """
        Override default_get to prefill sale order and product return moves.
        """
        res = super(ReturnPicking, self).default_get(fields)

        if self.env.context.get('active_id') and self.env.context.get('active_model') == 'sale.order':
            if len(self.env.context.get('active_ids', list())) > 1:
                raise UserError("You may only return one sale order at a time.")
            order_id = self.env['sale.order'].browse(self.env.context.get('active_id'))
            if order_id.exists():
                # Update the result dictionary with the sale order ID and product return moves
                res.update({'sale_order_id': order_id.id,
                            'product_return_moves': self._prepare_product_return_moves(order_id)})
        return res

    def _prepare_product_return_moves(self, order_id):
        """
        Prepare product return moves for the given sale order.
        """
        # Initialize the product return moves list with a command to clear existing moves
        product_return_moves = [(5,)]
        # Search for stock moves related to the sale order lines that are not yet refunded
        stock_move_ids = self.env["stock.move"].search([
            ("sale_line_id", "in", order_id.order_line.ids),
            ("picking_id", "=", False),
        ])
        # Iterate through each stock move to prepare return move data
        for move in stock_move_ids:
            if move.state == 'cancel' or  move.location_dest_usage == 'inventory':
                continue
            product_return_moves_data = self._prepare_stock_return_picking_line_vals_from_move_ts(move)
            product_return_moves.append((0, 0, product_return_moves_data))
        return product_return_moves

    def action_create_returns_ts(self):
        """
        Create return moves for products and validate them.
        """
        returned_lines = False
        # Iterate through each product return move to create return lines
        for return_line in self.product_return_moves:
            if not return_line.move_id:
                raise UserError("You have manually created product lines, please delete them to proceed.")
            if return_line.quantity:
                returned_lines = True
                # Create and process the return move for the given line
                self._create_and_process_return_move(return_line)

        # Raise an error if no valid return lines were found
        if not returned_lines and not self.env.context.get('skip_error', False):
            raise UserError("Please specify at least one non-zero quantity.")
        return True

    def _create_and_process_return_move(self, return_line):
        """
        Create and process a return move for the given return line.
        """
        # Prepare the default values for the return move
        vals = return_line._prepare_move_default_values(self.env['stock.picking'])
        # Create a copy of the original move with the updated values
        return_move = return_line.move_id.copy(vals)

        # Prepare values for linking the return move to original and destination moves
        return_vals = {'picked': True}
        move_orig_to_link = self._get_original_moves_to_link(return_line)
        move_dest_to_link = self._get_destination_moves_to_link(return_line)
        return_vals['move_orig_ids'] = [(4, m.id) for m in move_orig_to_link]
        return_vals['move_dest_ids'] = [(4, m.id) for m in move_dest_to_link]
        # Write the links to the return move
        return_move.write(return_vals)

        # Assign, set the done quantity, and validate the return move
        return_move._action_assign()
        return_move._set_quantity_done(return_line.quantity)
        return_move._action_done()

    def _get_original_moves_to_link(self, return_line):
        """
        Get original moves to link for the return line.
        """
        # Collect moves that were returned from the destination moves of the original move
        move_orig_to_link = return_line.move_id.move_dest_ids.mapped('returned_move_ids')
        # Add the original move itself to the list
        move_orig_to_link |= return_line.move_id
        # Add moves that are linked to the destination moves but are not canceled
        move_orig_to_link |= return_line.move_id.mapped('move_dest_ids').filtered(lambda m: m.state not in ('cancel')).mapped('move_orig_ids').filtered(
            lambda m: m.state not in ('cancel'))
        return move_orig_to_link

    def _get_destination_moves_to_link(self, return_line):
        """
        Get destination moves to link for the return line.
        """
        # Collect moves that were returned from the original moves of the return line
        move_dest_to_link = return_line.move_id.move_orig_ids.mapped('returned_move_ids')
        # Add moves that are linked to the original moves but are not canceled
        move_dest_to_link |= return_line.move_id.move_orig_ids.mapped('returned_move_ids').mapped('move_orig_ids').filtered(lambda m: m.state not in ('cancel')).mapped(
            'move_dest_ids').filtered(lambda m: m.state not in ('cancel'))
        return move_dest_to_link
