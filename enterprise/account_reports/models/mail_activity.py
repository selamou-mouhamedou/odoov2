# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models, _


class AccountTaxReportActivity(models.Model):
    _inherit = "mail.activity"

    account_tax_closing_params = fields.Json(string="Tax closing additional params")

    def _action_done(self, feedback=False, attachment_ids=None):
        tax_report_activities = self.filtered(
            lambda act:
                act.automated
                and act.res_model == 'account.move'
                and act.activity_category == 'tax_report'
                # lot of previous condition in order to avoid that `ref` query
                and act.activity_type_id == self.env.ref('account_reports.mail_activity_type_tax_report_to_be_sent', raise_if_not_found=False)
                and (move := self.env['account.move'].browse(act.res_id))
                and move._get_tax_to_pay_on_closing() > 0
        )

        if not tax_report_activities:
            return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

        mat_pay_tax_repo_xml_id = 'account_reports.mail_activity_type_tax_report_to_pay'
        pay_tax_activity_type = self.env.ref(mat_pay_tax_repo_xml_id, raise_if_not_found=False)
        # As this is introduced in stable, we ensure data exists by creating them on the fly if needed
        if not pay_tax_activity_type:
            pay_tax_activity_type = self.env['mail.activity.type'].sudo()._load_records([{
                'xml_id': mat_pay_tax_repo_xml_id,
                'noupdate': False,
                'values': {
                    'name': 'Pay Tax',
                    'summary': 'Tax is ready to be paid',
                    'category': 'tax_report',
                    'delay_count': '0',
                    'delay_unit': 'days',
                    'delay_from': 'previous_activity',
                    'res_model': 'account.move',
                    'chaining_type': 'suggest',
                }
            }])

        for activity in tax_report_activities:
            move = self.env['account.move'].browse(activity.res_id)
            period_start, period_end = move.company_id._get_tax_closing_period_boundaries(move.date, move.tax_closing_report_id)
            period_desc = move.company_id._get_tax_closing_move_description(move.company_id._get_tax_periodicity(move.tax_closing_report_id), period_start, period_end, move.fiscal_position_id, move.tax_closing_report_id)
            move.with_context(mail_activity_quick_update=True).activity_schedule(
                act_type_xmlid=mat_pay_tax_repo_xml_id,
                summary=_("Pay tax: %s", period_desc),
                date_deadline=fields.Date.context_today(move),
                user_id=activity.user_id.id or self.env.user.id,
            )

        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

    def action_open_tax_activity(self):
        self.ensure_one()
        if self.activity_type_id == self.env.ref('account_reports.mail_activity_type_tax_report_to_pay'):
            move = self.env['account.move'].browse(self.res_id)
            return move._action_tax_to_pay_wizard()

        journal = self.env['account.journal'].browse(self.res_id)
        options = {}
        if self.account_tax_closing_params:
            options = self.env['account.move']._get_tax_closing_report_options(
                journal.company_id,
                self.env['account.fiscal.position'].browse(self.account_tax_closing_params['fpos_id']) if self.account_tax_closing_params['fpos_id'] else False,
                self.env['account.report'].browse(self.account_tax_closing_params['report_id']),
                fields.Date.from_string(self.account_tax_closing_params['tax_closing_end_date'])
            )
        action = self.env["ir.actions.actions"]._for_xml_id("account_reports.action_account_report_gt")
        action.update({'params': {'options': options, 'ignore_session': True}})
        return action
