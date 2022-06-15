# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import traceback
from io import BytesIO
from datetime import datetime
from lxml import etree
from odoo.exceptions import ValidationError, UserError
from odoo import api, fields, models, registry, SUPERUSER_ID, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, pycompat
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

TIMEOUT = 60


class SyncDocumentType(models.Model):
    _name = 'sync.document.type'
    _description = 'Sync Document Type'

    # ------------------
    # Fields Declaration
    # ------------------

    name = fields.Char('Name', required=True, translate=True, index=True, copy=False)
    active = fields.Boolean(string='Active', default=True)
    op_type = fields.Selection(selection=[
        ('in', 'Import Documents'),
        ('in-mv', 'Import Documents and Move files'),
        ('out', 'Export Documents'),
    ], string='Operation Type', required=True, copy=False)
    doc_code = fields.Selection(selection=[('none', 'No Document')], string='Document Code (EDI)',
                                required=True, copy=False)


class EDIConfig(models.Model):
    # Private Attributes
    _name = 'edi.config'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'EDI Configurations'
    _order = 'sequence, id'

    # -------------------
    # Fields Declarations
    # -------------------

    name = fields.Char('EDI Title', required=True, translate=True, index=True, copy=False)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(help='Determine the processing order', default=24)
    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.user.company_id.id, required=True)
    ftp_host = fields.Char(string='Host', required=True)
    ftp_port = fields.Integer(string='Port', required=True, default=22)
    ftp_portocol = fields.Selection(selection=[
        ('ftp', 'FTP - File Transfer Protocol'),
        ('sftp', 'SFTP - SSH File Transfer Protocol')
    ], string='Protocol', required=True, default='ftp')
    ftp_login = fields.Char(string='Username', required=True)
    ftp_password = fields.Char(string='Password', required=True)
    sync_action_ids = fields.One2many(comodel_name='edi.sync.action',
                                      inverse_name='config_id',
                                      column1='config_id', column2='sync_action_id',
                                      string='Synchronization Actions')
    note = fields.Html(string='Notes')
    logging_ids = fields.One2many('edi.logging', 'config_id', string='EDI Logging')
    user_id = fields.Many2one(comodel_name='res.users', string='Responsible (User)')

    def test_provider_connection(self):
        for server in self:
            ftp_connection = server._get_provider_connection()
            try:
                ftp_connection._connect()
            except Exception as ftpe:
                raise ValidationError('Connection Test Failed! Here is what we got instead:\n %s' % (ftpe))
            finally:
                try:
                    ftp_connection._disconnect()
                except:
                    pass
        raise UserError(_('Connection Test Succeeded! Everything seems properly set up!'))

    # --------------------------
    # Private & Business Methods
    # --------------------------

    def _get_provider_config(self, config=None):
        if not config:
            config = {}
        config.update({
            'host': self.ftp_host,
            'port': self.ftp_port,
            'login': self.ftp_login,
            'password': self.ftp_password,
            'repin': '/',
        })
        return config

    def _get_provider_connection(self):
        config = self._get_provider_config()
        from importlib import import_module
        connector = import_module('odoo.addons.edi_document_sync.models.%s' % ('%s_connection' % self.ftp_portocol))
        return getattr(connector, '%sConnection' % self.ftp_portocol.upper())(config=config)

    def do_doucument_sync(self):
        for config in self:
            config.sync_action_ids.do_doc_sync_user()
        return True

    def _read_csv(self, csv_data, encoding='utf-8', separator=','):
        """ Returns file length and a CSV-parsed list of all non-empty lines in the file.
        """
        csv_data = csv_data or b''
        if not csv_data:
            return ()

        if encoding != 'utf-8':
            csv_data = csv_data.decode(encoding).encode('utf-8')

        csv_iterator = pycompat.csv_reader(
            BytesIO(csv_data),
            quotechar='"',
            delimiter=separator)

        content = [
            row for row in csv_iterator
            if any(x for x in row if x.strip())
        ]

        # return the file length as first value
        return len(content), content


class EDISyncAction(models.Model):
    # Private Attributes
    _name = 'edi.sync.action'
    _description = 'EDI Synchronization Actions'
    _order = 'sequence, doc_type_id'
    _rec_name = 'doc_type_id'

    # -------------------
    # Fields Declarations
    # -------------------

    active = fields.Boolean(string='Active', default=True)
    sequence = fields.Integer(string='Sequence',
                              help='Determine the action processing order', default=10)
    config_id = fields.Many2one(comodel_name='edi.config', string='EDI Configuration',
                                ondelete='restrict', required=True, index=True, copy=False)
    doc_type_id = fields.Many2one(comodel_name='sync.document.type', string='Document Action',
                                  required=True, index=True, copy=False)
    op_type = fields.Selection(related='doc_type_id.op_type', string='Action Operation Type',
                               store=True, readonly=True)
    dir_path = fields.Char(string='Directory Path', required=True, default='/',
                           help="Directory path on FTP host, used for importing or expoerting files."
                                "'/' is root path in ftp host and path should always start with same")
    dir_mv_path = fields.Char(string='Move Directory Path', default='/',
                              help="Directory path on FTP host, used for moving file to after importing files."
                                   "'/' is root path in ftp host and path should always start with same.")
    file_expr = fields.Char(string='Files filter expression', default='*.xml')
    last_sync_date = fields.Datetime(string='Last Synchronized On')
    max_file_to_process = fields.Integer(default=5, string='Maximum file to process',
                                         help="maximun file to process in single cron cycle")
    doc_type_code = fields.Selection(related="doc_type_id.doc_code", readonly=True)
    company_id = fields.Many2one(related='config_id.company_id', relation='res.company')

    # --------------------------
    # Business & Private Methods
    # --------------------------

    def do_doc_sync_user(self, use_new_cursor=False, company_id=False):
        self._do_doc_sync_cron(self, True)
        return True

    def _do_sync_sftp_protocol(self, sync_action, use_new_cursor, cr):
        self._do_sync_ftp_protocol(sync_action, use_new_cursor, cr)

    def _do_sync_json_api_protocol(self, sync_action, use_new_cursor, cr):
        values = {'company_id': sync_action.config_id.company_id}
        doc_action = sync_action.doc_type_id.doc_code
        sync_method = '_do_%s' % doc_action
        if hasattr(sync_action.doc_type_id, sync_method):
            _logger.info('running method `%s` for the synchronization action: '
                         '%d.' % (sync_method, sync_action.id))
            result = getattr(sync_action.doc_type_id, sync_method)(sync_action, values)
            if result:
                sync_action.last_sync_date = fields.Datetime.now()
            if use_new_cursor:
                cr.commit()
        else:
            _logger.warning('The method `%s` does not exist on synchronization action: '
                            '%d.' % (sync_method, sync_action.id))

    def _do_sync_ftp_protocol(self, sync_action, use_new_cursor, cr):
        values = {'company_id': sync_action.config_id.company_id}
        doc_action = sync_action.doc_type_id.doc_code
        sync_method = '_do_%s' % doc_action
        doc_action = sync_action.doc_type_id.doc_code
        conn = sync_action.config_id._get_provider_connection()
        if hasattr(sync_action.doc_type_id, sync_method) and conn:
            _logger.info('running method `%s` for the synchronization action: '
                         '%d.' % (sync_method, sync_action.id))
            with self._cr.savepoint():
                result = getattr(sync_action.doc_type_id, sync_method)(conn, sync_action, values)
                if result:
                    sync_action.last_sync_date = fields.Datetime.now()
            if use_new_cursor:
                cr.commit()
        else:
            _logger.warning('The method `%s` does not exist on synchronization action: '
                            '%d.' % (sync_method, sync_action.id))

    @api.model
    def _do_doc_sync_cron(self, sync_action_id=False, use_new_cursor=False, company_id=False):
        '''
        Call the doucment code method added by the modules.
        '''
        sync_action_todo = self
        cr = self.env.cr
        if sync_action_id:
            if isinstance(sync_action_id, (list, tuple)):
                sync_action_todo |= self.browse(sync_action_id)
            elif isinstance(sync_action_id, models.BaseModel):
                sync_action_todo |= sync_action_id
            else:
                _logger.error('Invalid sync_action_id param  passed (hint: pass <list>, <tuple> '
                              'or recordset of type <EDISyncAction|BaseModel>).')
        else:
            sync_action_todo = self.search(['|',
                                            ('last_sync_date', '<', fields.Datetime.now()),
                                            ('last_sync_date', '=', False)
                                            ])
        for sync_action in sync_action_todo:
            try:
                doc_action = sync_action.doc_type_id.doc_code
                sync_method = '_do_%s' % doc_action
                values = {'company_id': sync_action.config_id.company_id}
                if hasattr(sync_action.doc_type_id, sync_method):
                    _logger.info('running method `%s` for the synchronization action: '
                                 '%d.' % (sync_method, sync_action.id))
                    with self._cr.savepoint():
                        result = getattr(sync_action.doc_type_id, sync_method)(sync_action, values)
                        if result:
                            sync_action.last_sync_date = fields.Datetime.now()
                    if use_new_cursor:
                        cr.commit()
                else:
                    _logger.warning('The method `%s` does not exist on synchronization action: '
                                    '%d.' % (sync_method, sync_action.id))
            except Exception:
                if use_new_cursor:
                    cr.rollback()
                traceback_message = traceback.format_exc()
                _logger.error(traceback_message)
        return True


class EDILogging(models.Model):
    _name = 'edi.logging'
    _description = 'EDI Logging'
    _order = 'id desc'

    name = fields.Char(string='Log')
    code = fields.Text(string='Error Code')
    edi_sync_id = fields.Many2one('edi.sync.action')
    config_id = fields.Many2one('edi.config')
