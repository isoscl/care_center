from datetime import date, timedelta

from odoo import models, fields, api, _


class ProjectTask(models.Model):
    _name = 'project.task'
    _inherit = ['care_center.base', 'project.task']

    medium_id = fields.Many2one('utm.medium', 'Medium',
                                help="This is the method of delivery. "
                                     "Ex: Email / Phonecall / API / Website")
    description = fields.Html('Private Note')
    task_active = fields.Boolean(compute='_task_active')

    @api.multi
    def _task_active(self):
        if not self.active:
            self.task_active = False
        elif self.stage_id.fold:
            self.task_active = False
        else:
            self.task_active = True

    @api.model
    def message_new(self, msg, custom_values=None):
        """Override to set message body to be in the
        Ticket Description rather than first Chatter message
        """
        custom_values = dict(custom_values or {})
        if 'medium_id' not in custom_values and 'medium_id' not in msg:
            custom_values['medium_id'] = self.env.ref('utm.utm_medium_email').id
        if not msg.get('description', None):
            custom_values['description'] = msg.get('body', None)
        msg['body'] = None
        return super(ProjectTask, self).message_new(msg, custom_values=custom_values)

    @api.multi
    def message_update(self, msg, update_vals=None):
        """Override to re-open task if it was closed."""
        update_vals = dict(update_vals or {})
        if not self.active:
            update_vals['active'] = True
        return super(ProjectTask, self).message_update(msg, update_vals=update_vals)

    @api.model
    def api_message_new(self, msg):
        """
        Create a Ticket via API call. Should be callable with the same signature as
        python's sending emails.

        @param dict msg: dictionary of message variables 
       :rtype: int
       :return: the id of the new Ticket
        """

        Tag = self.env['project.tags']
        Project = self.env['project.project']
        project = msg.get('project', None) and Project.search([('name', '=', msg['project'])])

        data = {
            'project_id': project and project.id,
            'medium_id': self.env.ref('care_center.utm_medium_api').id,
            'tag_ids': [(6, False, [tag.id for tag in Tag.search([('name', 'in', msg.get('tags', []))])])],
        }

        if 'partner_id' not in msg and project and project.partner_id:
            data['partner_id'] = project.partner_id.id
            data['email_from'] = project.partner_id.email

        # Python's CC email param takes a list, so cast to string if necessary
        if isinstance(msg.get('cc', ''), (list, tuple)):
            msg['cc'] = ','.join(msg['cc'])

        msg.update(data)

        return super(ProjectTask, self).message_new(msg, custom_values=data)

    @api.multi
    def redirect_task_view(self):
        """Enable redirecting to a Ticket when created from a phone call."""
        self.ensure_one()

        form_view = self.env.ref('project.view_task_form2')
        tree_view = self.env.ref('project.view_task_tree2')
        kanban_view = self.env.ref('project.view_task_kanban')
        calendar_view = self.env.ref('project.view_task_calendar')
        graph_view = self.env.ref('project.view_project_task_graph')

        return {
            'name': _('Ticket'),
            'view_type': 'form',
            'view_mode': 'tree, form, calendar, kanban',
            'res_model': 'project.task',
            'res_id': self.id,
            'view_id': False,
            'views': [
                (form_view.id, 'form'),
                (tree_view.id, 'tree'),
                (kanban_view.id, 'kanban'),
                (calendar_view.id, 'calendar'),
                (graph_view.id, 'graph')
            ],
            'type': 'ir.actions.act_window',
        }

    @api.onchange('partner_id')
    def _partner_id(self):
        """
        Filter Tickets by Partner, including all
        Tickets of Partner Parent or Children
        """
        partner = self.partner_id

        if not partner:
            domain = []

        else:

            partner_ids = self.get_partner_ids()
            domain = self.get_partner_domain(partner_ids)

            # Only reset project if the Partner is set, and is
            # NOT related to the current Contact selected
            proj_partner = self.project_id.partner_id and self.project_id.partner_id.id
            if proj_partner and proj_partner not in partner_ids:
                self.project_id = None

        return {
            'domain': {
                'project_id': domain,
            },
        }

    @api.onchange('project_id')
    def _project_id(self):

        if not self.date_deadline:
            self.date_deadline = fields.Date.to_string(date.today() + timedelta(hours=48))

        if self.env.context.get('project_tag', None):
            if not self.tag_ids:
                self.tag_ids = self.env['project.tags'].search([('name', '=', self.env.context['project_tag'])])

    # @api.constrains('project_id')
    # def check_relationships(self):
    #     """
    #     If project has partner assigned, it must
    #     be related to the Ticket Partner
    #     """
    #     proj_partner = self.project_id.partner_id.id
    #     if not proj_partner:
    #         return
    #
    #     ticket_partner = self.partner_id and self.partner_id.id
    #     ticket_parent_partner = self.partner_id and \
    #                            self.partner_id.parent_id and \
    #                            self.partner_id.parent_id.id
    #
    #     if proj_partner != ticket_partner and proj_partner != ticket_parent_partner:
    #         raise ValidationError(
    #             'Project Contact and Ticket Contact must be the same, '
    #             'or have the same parent Company.'
    #         )

    @api.model
    def message_get_reply_to(self, res_ids, default=None):
        """ Override to get the reply_to of the parent project. """
        tasks = self.browse(res_ids)
        project_ids = set(tasks.mapped('project_id').ids)
        aliases = self.env['project.project'].message_get_reply_to(list(project_ids), default=default)
        return dict((task.id, aliases.get(task.project_id and task.project_id.id or 0, False)) for task in tasks)

    def email_the_customer(self):
        """
        Helper function to be called from close_ticket or email_customer.
        Can't be a decorated and be called from other dectorated methods
        """

        compose_form = self.env.ref('mail.email_compose_message_wizard_form', False)
        template = self.env['mail.template'].search([('name', '=', 'CF Ticket - Close')])
        ctx = {
            'default_model': 'project.task',
            'default_res_id': self.id,
            'default_use_template': bool(template),
            'default_template_id': template.id,
            'default_composition_mode': 'comment',
        }
        return {
            'name': 'Compose Email',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form.id, 'form')],
            'view_id': compose_form.id,
            'target': 'new',
            'context': ctx,
        }

    @api.multi
    def claim_ticket(self):
        self.ensure_one()
        self.user_id = self._uid

    @api.multi
    def close_ticket(self):
        self.ensure_one()
        self.stage_id = self.env['project.task.type'].search([('name', '=', 'Done')])
        if self.active:
            self.toggle_active()
        return self.email_the_customer()

    @api.multi
    def reopen_ticket(self):
        self.ensure_one()
        self.stage_id = self.env['project.task.type'].search([('name', '=', 'In Progress')])
        self.active = True
        self.date_close = None

    @api.multi
    def email_customer(self):
        """
        Open a window to compose an email
        """
        self.ensure_one()
        return self.email_the_customer()

    @api.multi
    def toggle_active(self):
        """ Inverse the value of the field ``active`` on the records in ``self``. """

        for record in self:
            if record.active:
                self.date_close = fields.Datetime.now()
            else:
                self.date_close = None

        super(ProjectTask, self).toggle_active()