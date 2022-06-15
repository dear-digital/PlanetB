# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import tempfile
import os
import pysftp
import traceback
import csv
from datetime import timedelta
from odoo import api, fields, models, _, SUPERUSER_ID

_logger = logging.getLogger(__name__)


class SyncDocumentType(models.Model):
    _inherit = 'sync.document.type'

    doc_code = fields.Selection(selection_add=[
        ('export_sale_order_document', 'Export Sale Order Document'),
        ('import_sale_order_document', 'Import Sale Order Document')],
        ondelete={'export_sale_order_document': 'cascade', 'import_sale_order_document': 'cascade'})

    def _get_category_ids_to_ignore(self):
        ignore_ids = []
        ignorable_category = self.env['product.category'].search([('ignored_in_webship', '=', True)])
        ignore_ids.extend(ignorable_category.ids)
        ignore_ids.extend(ignorable_category.mapped('child_id').ids)
        return ignore_ids

    def _prepare_importable_data(self, rows_to_import, fields_for_rows):
        return [
            dict(zip(fields_for_rows, row))
            for row in rows_to_import
        ]

    def _get_fields_to_export(self):
        """Sequence of the fields is important, as based on this sequence fields value will be mapped in csv file"""
        return [
            'client_name',
            'client_contact',
            'client_vat',
            'shipping_address_1',
            'shipping_address_2',
            'shipping_postal_code',
            'shipping_city',
            'shipping_country',
            'client_email',
            'sku',
            'quantity',
            'connector',
            'reference',
            'status',
        ]

    def _do_export_sale_order_document(self, sync_action_id, values):
        yesterday = fields.Datetime.now() - timedelta(days=1)
        sale_orders = self.env['sale.order'].search([('date_order', '>=', yesterday), ('state', '=', 'sale')])
        unprocessed_order_ids = []
        data_list = []
        log_note = []
        order_wise_log_note = []
        for order_line in sale_orders.order_line.filtered(
                lambda line: line.product_id.categ_id.id not in self._get_category_ids_to_ignore()):
            all_required_fields = True
            sale_order = order_line.order_id
            partner = sale_order.partner_shipping_id
            client_name = partner.parent_id.name if partner.parent_id else partner.name
            client_contact = partner.name if partner.parent_id and partner.name else ""
            client_vat = partner.vat if partner.vat else ""
            shipping_address_1 = partner.street if partner.street else ""
            shipping_address_2 = partner.street2 if partner.street2 else ""
            shipping_postal_code = partner.zip if partner.zip else ""
            shipping_city = partner.city if partner.city else ""
            shipping_country = partner.country_id.code if partner.country_id else ""
            client_email = partner.email if partner.email else ""
            sku = order_line.product_id.default_code or ""
            # if order_line.product_packaging_id:
            #     sku_value = order_line.product_packaging_id.barcode if order_line.product_packaging_id.barcode else ""
            # if not bool(sku_value):
            #     sku_value = order_line.product_id.barcode
            # sku = sku_value
            quantity = order_line.product_uom_qty
            connector = sale_order.name
            reference = sale_order.name
            required_fields_filled = all(
                [client_name, shipping_address_1, shipping_postal_code, shipping_city, shipping_country, sku, quantity])
            if not required_fields_filled:
                required_fields_dic = {'client_name': client_name, 'shipping_address_1': shipping_address_1,
                                       'shipping_postal_code': shipping_postal_code, 'shipping_city': shipping_city,
                                       'shipping_country': shipping_country, 'sku': sku, 'quantity': quantity}
                for key, values in required_fields_dic.items():
                    if not values:
                        log_note.append(
                            ["missing field: " + key + " For line:" + order_line.display_name, sale_order.id,
                             sale_order.name])
                all_required_fields = False
                unprocessed_order_ids.append(sale_order.id)
            data_list.append([[client_name, client_contact, client_vat, shipping_address_1, shipping_address_2,
                               shipping_postal_code, shipping_city, shipping_country, client_email, sku, quantity,
                               connector, reference, 'ready-to-pick'], all_required_fields])
        unprocessed_order_ids = list(set(unprocessed_order_ids))
        for id in unprocessed_order_ids:
            order_wise_log_note.append([i for i in log_note if i[1] == id])
        list_to_make_csv = [i[0] for i in data_list if i[1]]
        for order_wise_log in order_wise_log_note:
            combined_message = ""
            for message in order_wise_log:
                combined_message = combined_message + "\n" + message[0]
            self.env['edi.logging'].create({
                'name': order_wise_log[0][2],
                'code': combined_message,
                'edi_sync_id': sync_action_id.id,
                'config_id': sync_action_id.config_id.id,
            })
        if len(list_to_make_csv):
            try:
                temp_dir = tempfile.TemporaryDirectory()
                completeName = os.path.join(temp_dir.name, 'orders.csv')
                with open(completeName, mode='w') as order_line_file:
                    writer = csv.writer(order_line_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
                    writer.writerow(self._get_fields_to_export())
                    writer.writerows(list_to_make_csv)
                    order_line_file.close()
                host = sync_action_id.config_id.ftp_host
                user = sync_action_id.config_id.ftp_login
                key = sync_action_id.config_id.ftp_password
                cnopts = pysftp.CnOpts()
                cnopts.hostkeys = None
                with pysftp.Connection(host, username=user, password=key, cnopts=cnopts) as sftp:
                    # sftp.cd('/var/webshipexport')
                    sftp.cd(sync_action_id.dir_path + '/ orders.csv')
                    sftp.put(completeName, preserve_mtime=True)
                    sftp.close()
                    processed_orders = sale_orders.filtered(lambda order: order.id not in unprocessed_order_ids)
                # Remove temp directory
                temp_dir.cleanup()
                processed_orders_name = processed_orders.mapped('name')
                success_message = 'Processed Orders are : \n' + '\n'.join(processed_orders_name)
                self.env['edi.logging'].create({
                    'name': 'Exported Orders (Success)',
                    'code': success_message,
                    'edi_sync_id': sync_action_id.id,
                    'config_id': sync_action_id.config_id.id,
                })
            except Exception:
                traceback_message = traceback.format_exc()
                _logger.error(traceback_message)
                self.env['edi.logging'].create({
                    'name': 'EXPORT Failed',
                    'code': traceback_message,
                    'edi_sync_id': sync_action_id.id,
                    'config_id': sync_action_id.config_id.id,
                })
        else:
            self.env['edi.logging'].create({
                'name': 'NO ORDERS EXPORTED',
                'code': "There is no relatable data found to be exported!",
                'edi_sync_id': sync_action_id.id,
                'config_id': sync_action_id.config_id.id,
            })

    def _do_import_sale_order_document(self, sync_action_id, values):
        """
            Method to import Orders from ftp server and update fields in sale order for webship,
            validate deliveries as well.
        """
        host = sync_action_id.config_id.ftp_host
        user = sync_action_id.config_id.ftp_login
        key = sync_action_id.config_id.ftp_password
        cnopts = pysftp.CnOpts()
        cnopts.hostkeys = None
        fields_from_the_imports = []
        rows_of_csv = []
        try:
            temp_dir = tempfile.TemporaryDirectory()
            completeName = os.path.join(temp_dir.name, 'orders.csv')
            with pysftp.Connection(host, username=user, password=key, cnopts=cnopts) as sftp:
                sftp.cd('/var/webshipimport')
                sftp.get('orders.csv', completeName)
                with open(completeName, newline='') as f:
                    csvreader = csv.reader(f)
                    fields_from_the_imports.append(next(csvreader))
                    for row in csvreader:
                        rows_of_csv.append(row)
                    f.close()
                sftp.close()
            temp_dir.cleanup()
        except Exception:
            traceback_message = traceback.format_exc()
            _logger.error(traceback_message)
            self.env['edi.logging'].create({
                'name': 'IMPORT Failed',
                'code': traceback_message,
                'edi_sync_id': sync_action_id.id,
                'config_id': sync_action_id.config_id.id,
            })
        if rows_of_csv:
            sale_orders_imported = []
            unmatched_orders = []
            sale_order_processed = []
            validated_picking = []
            not_validated_picking = []
            picking_not_considered_order = []
            datas = self._prepare_importable_data(rows_of_csv, fields_from_the_imports[0])
            vals = {}
            for line in datas:
                if line['reference'] in vals.keys():
                    vals[line['reference']].append(
                        {'sku': line['sku'], 'status': line['status'], 'qty': line['quantity'],
                         'order_number': line['order_number'], 'tracking_numbers': line['tracking_numbers']})
                else:
                    vals[line['reference']] = [{'sku': line['sku'], 'status': line['status'], 'qty': line['quantity'],
                                                'order_number': line['order_number'],
                                                'tracking_numbers': line['tracking_numbers']}]
            for key, value in vals.items():
                sale_orders_imported.append(key)
                sale_order = self.env['sale.order'].search([('name', '=', key)], limit=1)
                if sale_order:
                    sale_order.webship_status = value[0]['status']
                    sale_order.webship_reference = value[0]['order_number']
                    sale_order.webship_track_and_trace = value[0]['tracking_numbers']
                    if value[0]['status'] == 'completed' and sale_order:
                        sale_order_processed.append(sale_order.name + " : completed state")
                        validate = []
                        number_of_sale_line = len(
                            sale_order.order_line.filtered(lambda x: not x.product_id.categ_id.ignored_in_webship))
                        if number_of_sale_line == len(value):
                            for line in sale_order.order_line.filtered(
                                    lambda x: not x.product_id.categ_id.ignored_in_webship):
                                sku_in_line = line.product_packaging_id.barcode if line.product_packaging_id else line.product_id.barcode
                                matched_line = list(filter(lambda x: x['sku'] == sku_in_line, value))
                                quantity_to_check = float(matched_line[0]['qty']) if bool(matched_line) else 0
                                if quantity_to_check == line.product_uom_qty:
                                    validate.append(True)
                                else:
                                    validate.append(False)
                                    break
                        else:
                            not_validated_picking.append(sale_order.name + " : Lines/Qty mismatch")
                        if all(validate) and (len(validate) == number_of_sale_line):
                            for picking in sale_order.picking_ids:
                                if picking.state == 'assigned':
                                    try:
                                        for move_line in picking.move_line_ids:
                                            move_line.qty_done = move_line.product_uom_qty
                                        picking.button_validate()
                                        validated_picking.append(picking.name)
                                    except Exception:
                                        traceback_message = traceback.format_exc()
                                        _logger.error(traceback_message)
                                        not_validated_picking.append(picking.name + ": " + traceback_message)
                                else:
                                    not_validated_picking.append(picking.name + " is in state : " + picking.state)
                        else:
                            invalid_quantity_message = " : Lines matched, but quantity for lines are invalid! "
                            not_validated_picking.append(sale_order.name + invalid_quantity_message)
                            _logger.error(invalid_quantity_message)
                    else:
                        picking_not_considered_order.append(sale_order.name)
                else:
                    unmatched_orders.append(key)
            success_message = 'Total Imported Sale Orders are : \n' + '\n'.join(
                sale_orders_imported) if sale_orders_imported else ""
            validate_message = '\n\nValidated Pickings are: \n' + '\n'.join(
                validated_picking) if validated_picking else ""
            process_message = '\n\nProcessed sale order :\n ' + '\n'.join(
                sale_order_processed) if sale_order_processed else ""
            failure_message_for_transfers = '\n\nTransfers with issue in Validation: \n' + '\n'.join(
                not_validated_picking) if not_validated_picking else ""
            failure_message_for_picking_unconsidered = '\n\nPicking not considered for Orders: \n' + '\n'.join(
                picking_not_considered_order) if picking_not_considered_order else ""
            unmatched_orders_message = '\n\nUnmatched Orders are:  \n' + "\n".join(
                unmatched_orders) if unmatched_orders else ""
            self.env['edi.logging'].create({
                'name': 'Import Orders (Success)',
                'code': success_message + validate_message + process_message + failure_message_for_transfers + failure_message_for_picking_unconsidered + unmatched_orders_message,
                'edi_sync_id': sync_action_id.id,
                'config_id': sync_action_id.config_id.id,
            })
