# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals

import json

import frappe
from frappe import _, throw
from frappe.desk.form.assign_to import clear, close_all_assignments, add
from frappe.model.mapper import get_mapped_doc
from frappe.utils import add_days, cstr, date_diff, get_link_to_form, getdate, today
from frappe.utils.nestedset import NestedSet
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from collections import Counter


class CircularReferenceError(frappe.ValidationError): pass
class EndDateCannotBeGreaterThanProjectEndDateError(frappe.ValidationError): pass

class Task(NestedSet):
	nsm_parent_field = 'parent_task'

	def get_feed(self):
		return '{0}: {1}'.format(_(self.status), self.subject)

	def get_customer_details(self):
		cust = frappe.db.sql("select customer_name from `tabCustomer` where name=%s", self.customer)
		if cust:
			ret = {'customer_name': cust and cust[0][0] or ''}
			return ret

	def validate(self):
		self.validate_dates()
		self.validate_parent_project_dates()
		self.validate_status()
		self.update_depends_on()
		self.validate_default_project()
		self.update_completion_date()

	def before_save(self):
		change_idx = False
		parent_task = frappe.get_value("Task", self.name, "parent_task")
		if parent_task and not self.parent_task:
			dependent_task = frappe.get_value("Task", parent_task, "depends_on_tasks")
			dependent_task_list = dependent_task.split(',')
			for depended_task in dependent_task_list:
				if depended_task:
					dependent_task_name, dependent_task_idx = frappe.get_value("Task Depends On",
						filters={"task": depended_task, "parent": parent_task},
						fieldname=["name", "idx"])
					if depended_task == self.name:
						frappe.delete_doc('Task Depends On', dependent_task_name)
						change_idx = True
					elif change_idx:
						frappe.db.set_value("Task Depends On", dependent_task_name, "idx", (dependent_task_idx-1))
			dependent_task_list.remove(self.name)
			dependent_task_string = ','.join(map(str, dependent_task_list))
			frappe.db.set_value("Task", parent_task, "depends_on_tasks", dependent_task_string)


	def validate_dates(self):
		if self.exp_start_date and self.exp_end_date and getdate(self.exp_start_date) > getdate(self.exp_end_date):
			frappe.throw(_("{0} can not be greater than {1}").format(frappe.bold("Expected Start Date"), \
				frappe.bold("Expected End Date")))

		if self.act_start_date and self.act_end_date and getdate(self.act_start_date) > getdate(self.act_end_date):
			frappe.throw(_("{0} can not be greater than {1}").format(frappe.bold("Actual Start Date"), \
				frappe.bold("Actual End Date")))

	def validate_parent_project_dates(self):
		if not self.project or frappe.flags.in_test:
			return

		expected_end_date = frappe.db.get_value("Project", self.project, "expected_end_date")

		if expected_end_date:
			validate_project_dates(getdate(expected_end_date), self, "exp_start_date", "exp_end_date", "Expected")
			validate_project_dates(getdate(expected_end_date), self, "act_start_date", "act_end_date", "Actual")

	def validate_status(self):
		if self.status!=self.get_db_value("status") and self.status == "Completed":
			for d in self.depends_on:
				if frappe.db.get_value("Task", d.task, "status") not in ("Completed", "Closed"):
					frappe.throw(_("Cannot complete task {0} as its dependant tasks {1} are not completed / closed.").format(frappe.bold(self.name), frappe.bold(d.task)))

			if frappe.db.get_single_value("Projects Settings", "remove_assignment_on_task_completion"):
				close_all_assignments(self.doctype, self.name)

	def update_depends_on(self):
		depends_on_tasks = self.depends_on_tasks or ""
		for d in self.depends_on:
			if d.task and not d.task in depends_on_tasks:
				depends_on_tasks += d.task + ","
		self.depends_on_tasks = depends_on_tasks

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)
	
	def validate_default_project(self):
		""" Validate projects table in task."""

		_projects = [project.project for project in self.projects]
		if len(_projects) != len(list(set(_projects))):
			task_projects = _projects
			d = Counter(task_projects)
			res = [k for k, v in d.items() if v > 1]
			frappe.throw(_("Please remove duplicate project before proceeding. <ul><li>{0}</li></ul>".format('<li>'.join(res))))

		if len(self.projects) == 1:
			# auto-set default project if only one is found
			self.projects[0].is_default = 1
			self.project = self.projects[0].project
		elif len(self.projects) > 1:
			default_project = [project for project in self.projects if project.is_default]
			# prevent users from setting multiple default projects.
			if not default_project:
				frappe.throw(_("There must be atleast one default Project."))
			elif len(default_project) > 1:
				frappe.throw(_("There can be only one default project, found {0}.").format(len(default_project)))
			else:
				self.project = default_project[0].project
		elif not len(self.projects):
			# if no projects aviliable in projects make parent project empty
			self.project = None

	def update_completion_date(self):
		for task_project in self.projects:
			project = frappe.get_cached_doc("Project", task_project.project)
			complete_statuses = ['Completed']
			#needs to be replaced with a Table Multiselect after status is channged into a linked feild.
			if project.task_completion_statuses:
				complete_statuses = list(project.task_completion_statuses.split(","))
			if not task_project.completion_date and task_project.status in complete_statuses:
				task_project.completion_date = today()

	def on_update(self):
		self.update_nsm_model()
		self.check_recursion()
		self.reschedule_dependent_tasks()
		self.update_project()
		self.unassign_todo()
		self.populate_depends_on()
		self.notify()
		self.assign_todo()

	def unassign_todo(self):
		if self.status == "Completed" and frappe.db.get_single_value("Projects Settings", "remove_assignment_on_task_completion"):
			close_all_assignments(self.doctype, self.name)
		if self.status == "Closed":
			clear(self.doctype, self.name)

	def assign_todo(self):
		# Creating ToDo for assigned user.
		if self.assign_to:
			assign_to = [assign_to.user for assign_to in self.assign_to]
			add({
				'doctype': self.doctype,
				"name": self.name,
				'assign_to': assign_to
			})
	
	def update_total_expense_claim(self):
		self.total_expense_claim = frappe.db.sql("""select sum(total_sanctioned_amount) from `tabExpense Claim`
			where project = %s and task = %s and docstatus=1""",(self.project, self.name))[0][0]

	def update_time_and_costing(self):
		tl = frappe.db.sql("""select min(from_time) as start_date, max(to_time) as end_date,
			sum(billing_amount) as total_billing_amount, sum(costing_amount) as total_costing_amount,
			sum(hours) as time from `tabTimesheet Detail` where task = %s and docstatus=1"""
			,self.name, as_dict=1)[0]
		if self.status == "Open":
			self.status = "Working"
		self.total_costing_amount= tl.total_costing_amount
		self.total_billing_amount= tl.total_billing_amount
		self.actual_time= tl.time
		self.act_start_date= tl.start_date
		self.act_end_date= tl.end_date

	def update_project(self):
		if self.project and not self.flags.from_project:
			for task_project in self.projects:
				frappe.get_cached_doc("Project", task_project.project).update_project()

	def check_recursion(self):
		if self.flags.ignore_recursion_check: return
		check_list = [['task', 'parent'], ['parent', 'task']]
		for d in check_list:
			task_list, count = [self.name], 0
			while (len(task_list) > count ):
				tasks = frappe.db.sql(" select %s from `tabTask Depends On` where %s = %s " %
					(d[0], d[1], '%s'), cstr(task_list[count]))
				count = count + 1
				for b in tasks:
					if b[0] == self.name:
						frappe.throw(_("Circular Reference Error"), CircularReferenceError)
					if b[0]:
						task_list.append(b[0])

				if count == 15:
					break

	def reschedule_dependent_tasks(self):
		end_date = self.exp_end_date or self.act_end_date
		if end_date:
			for task_name in frappe.db.sql("""
				select name from `tabTask` as parent
				where parent.project = %(project)s
					and parent.name in (
						select parent from `tabTask Depends On` as child
						where child.task = %(task)s and child.project = %(project)s)
			""", {'project': self.project, 'task':self.name }, as_dict=1):
				task = frappe.get_doc("Task", task_name.name)
				if task.exp_start_date and task.exp_end_date and task.exp_start_date < getdate(end_date) and task.status == "Open":
					task_duration = date_diff(task.exp_end_date, task.exp_start_date)
					task.exp_start_date = add_days(end_date, 1)
					task.exp_end_date = add_days(task.exp_start_date, task_duration)
					task.flags.ignore_recursion_check = True
					task.save()

	def has_webform_permission(self):
		project_user = frappe.db.get_value("Project User", {"parent": self.project, "user":frappe.session.user} , "user")
		if project_user:
			return True

	def populate_depends_on(self):
		if self.parent_task:
			parent = frappe.get_doc('Task', self.parent_task)
			if not self.name in [row.task for row in parent.depends_on]:
				parent.append("depends_on", {
					"doctype": "Task Depends On",
					"task": self.name,
					"subject": self.subject
				})
				parent.save()

	def on_trash(self):
		if check_if_child_exists(self.name):
			throw(_("Child Task exists for this Task. You can not delete this Task."))

		self.update_nsm_model()

	def update_status(self):
		if self.status not in ('Closed', 'Completed') and self.exp_end_date:
			from datetime import datetime
			if self.exp_end_date < datetime.now().date():
				self.db_set('status', 'Overdue', update_modified=False)
				self.update_project()

	def notify(self):
		if not frappe.db.get_single_value("Projects Settings", "send_notifications_for_task"):
			return

		notification_doc = {
			'type': 'Notify',
			'document_type': self.doctype,
			'subject': _("Task {0} has been updated.").format("<a href='{0}'>{1}</a>".format(self.get_url(), frappe.bold(self.name))),
			'document_name': self.name,
			'from_user': frappe.session.user
		}

		enqueue_create_notification(self.get_assigned_users(), notification_doc)

		for user in self.get_assigned_users():
			if user == frappe.session.user:
				continue

			frappe.publish_realtime('show_notification_alert', message=notification_doc.get("subject"), after_commit=True, user=user)

@frappe.whitelist()
def check_if_child_exists(name):
	child_tasks = frappe.get_all("Task", filters={"parent_task": name})
	child_tasks = [get_link_to_form("Task", task.name) for task in child_tasks]
	return child_tasks


def get_project(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond
	return frappe.db.sql(""" select name from `tabProject`
			where %(key)s like %(txt)s
				%(mcond)s
			order by name
			limit %(start)s, %(page_len)s""" % {
				'key': searchfield,
				'txt': frappe.db.escape('%' + txt + '%'),
				'mcond':get_match_cond(doctype),
				'start': start,
				'page_len': page_len
			})

def get_team_members (doctype, txt, searchfield, start, page_len, filters):
	return frappe.get_all("Project Team Members", {"parent": filters.get("team")}, ["user", "user_name"], as_list=1)
	
@frappe.whitelist()
def set_multiple_status(names, status):
	names = json.loads(names)
	for name in names:
		task = frappe.get_doc("Task", name)
		task.status = status
		task.save()

def set_tasks_as_overdue():
	tasks = frappe.get_all("Task", filters={"status": ["not in", ["Closed", "Completed"]]}, fields=["name", "status", "review_date"])
	for task in tasks:
		if task.status == "Pending Review":
			if getdate(task.review_date) > getdate(today()):
				continue
		frappe.get_doc("Task", task.name).update_status()


@frappe.whitelist()
def make_timesheet(source_name, target_doc=None, ignore_permissions=False):
	def set_missing_values(source, target):
		target.append("time_logs", {
			"hours": source.actual_time,
			"completed": source.status == "Completed",
			"project": source.project,
			"task": source.name,
			"billable": source.billable
		})

	doclist = get_mapped_doc("Task", source_name, {
			"Task": {
				"doctype": "Timesheet"
			}
		}, target_doc, postprocess=set_missing_values, ignore_permissions=ignore_permissions)

	return doclist


@frappe.whitelist()
def get_children(doctype, parent, task=None, project=None, is_root=False):

	filters = [['docstatus', '<', '2']]

	if task:
		filters.append(['parent_task', '=', task])
	elif parent and not is_root:
		# via expand child
		filters.append(['parent_task', '=', parent])
	else:
		filters.append(['ifnull(`parent_task`, "")', '=', ''])

	if project:
		filters.append(['project', '=', project])

	tasks = frappe.get_list(doctype, fields=[
		'name as value',
		'subject as title',
		'is_group as expandable'
	], filters=filters, order_by='name')

	# return tasks
	return tasks

@frappe.whitelist()
def add_node():
	from frappe.desk.treeview import make_tree_args
	args = frappe.form_dict
	args.update({
		"name_field": "subject"
	})
	args = make_tree_args(**args)

	if args.parent_task == 'All Tasks' or args.parent_task == args.project:
		args.parent_task = None

	frappe.get_doc(args).insert()

@frappe.whitelist()
def add_multiple_tasks(data, parent):
	data = json.loads(data)
	new_doc = {'doctype': 'Task', 'parent_task': parent if parent!="All Tasks" else ""}
	new_doc['project'] = frappe.db.get_value('Task', {"name": parent}, 'project') or ""

	for d in data:
		if not d.get("subject"): continue
		new_doc['subject'] = d.get("subject")
		new_task = frappe.get_doc(new_doc)
		new_task.insert()

def on_doctype_update():
	frappe.db.add_index("Task", ["lft", "rgt"])

def validate_project_dates(project_end_date, task, task_start, task_end, actual_or_expected_date):
	if task.get(task_start) and date_diff(project_end_date, getdate(task.get(task_start))) < 0:
		frappe.throw(_("Task's {0} Start Date cannot be after Project's End Date.").format(actual_or_expected_date))

	if task.get(task_end) and date_diff(project_end_date, getdate(task.get(task_end))) < 0:
		frappe.throw(_("Task's {0} End Date cannot be after Project's End Date.").format(actual_or_expected_date))
