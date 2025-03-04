from __future__ import unicode_literals
import erpnext.education.utils as utils
import frappe

no_cache = 1

def get_context(context):
	# Load Query Parameters
	try:
		program = frappe.form_dict['program']
		content = frappe.form_dict['content']
		content_type = frappe.form_dict['type']
		course = frappe.form_dict['course']
		topic = frappe.form_dict['topic']
	except KeyError:
		frappe.local.flags.redirect_location = '/lms'
		raise frappe.Redirect


	# Check if user has access to the content
	has_program_access = utils.allowed_program_access(program)
	has_content_access = allowed_content_access(program, content, content_type)

	if frappe.session.user == "Guest" or not has_program_access or not has_content_access:
		frappe.local.flags.redirect_location = '/lms'
		raise frappe.Redirect


	# Set context for content to be displayer
	context.content = frappe.get_doc(content_type, content).as_dict()
	context.content_type = content_type
	context.program = program
	context.course = course
	context.topic = topic
	course = frappe.get_doc('Course', context.course)
	context.topics = course.get_topics()
	content_list = []
	for t in context.topics:
		for topic_content in t.topic_content:
			content_list.append({'content_type':topic_content.content_type, 'content':topic_content.content})

	# Set context for progress numbers
	context.position = content_list.index({'content': content, 'content_type': content_type})
	context.length = len(content_list)

	# Set context for navigation
	context.previous = utils.get_previous_content(content_list, context.position)
	context.next = utils.get_next_content(content_list, context.position)

def allowed_content_access(program, content, content_type):
	contents_of_program = frappe.db.sql("""select `tabTopic Content`.content, `tabTopic Content`.content_type
	from `tabCourse Topic`,
		 `tabProgram Course`,
		 `tabTopic Content`
	where `tabCourse Topic`.parent = `tabProgram Course`.course
			and `tabTopic Content`.parent = `tabCourse Topic`.topic
			and `tabProgram Course`.parent = %(program)s""", {'program': program})

	return (content, content_type) in contents_of_program
