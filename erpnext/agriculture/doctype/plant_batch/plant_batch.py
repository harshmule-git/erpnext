# -*- coding: utf-8 -*-
# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import ast

import frappe
from erpnext.agriculture.utils import create_project, create_tasks
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import getdate, nowdate, today, cstr
from erpnext.compliance.utils import make_integration_request, get_bloomtrace_client


class PlantBatch(Document):
	def validate(self):
		self.set_missing_values()

	def after_insert(self):
		self.create_plant_batch_project()

	def on_update(self):
		self.create_integration_request()

	def create_integration_request(self):
		make_integration_request(self.doctype, self.name, "Plant Batch")

	def set_missing_values(self):
		strain = frappe.get_doc('Strain', self.strain)

		if not self.plant_spacing_uom:
			self.plant_spacing_uom = strain.plant_spacing_uom

	def create_plant_batch_project(self):
		strain = frappe.get_doc('Strain', self.strain)
		if strain.cultivation_task:
			self.project = create_project(self.title, self.start_date, strain.period)
			create_tasks(strain.cultivation_task, self.project, self.start_date)

	def reload_linked_analysis(self):
		linked_doctypes = ['Soil Texture', 'Soil Analysis', 'Plant Analysis']
		required_fields = ['location', 'name', 'collection_datetime']
		output = {}

		for doctype in linked_doctypes:
			output[doctype] = frappe.get_all(doctype, fields=required_fields)

		output['Location'] = frappe.get_doc('Location', self.location)

		frappe.publish_realtime("List of Linked Docs",
								output, user=frappe.session.user)

	def append_to_child(self, obj_to_append):
		for doctype in obj_to_append:
			for doc_name in set(obj_to_append[doctype]):
				self.append(doctype, {doctype: doc_name})

		self.save()

	def destroy_plant_batch(self, destroy_count, reason):
		validate_quantities(self, destroy_count)
		destroyed_plant_log = frappe.get_doc(
			dict(
				doctype = 'Destroyed Plant Log',
				category_type = "Plant Batch",
				category = self.name,
				destroy_count = destroy_count,
				reason = reason,
				actual_date = getdate(nowdate())
			)
		).insert()
		destroyed_plant_log.submit()
		return destroyed_plant_log.name

	def split_plant_batch(self, split_count, new_plant_batch_id):
		if self.untracked_count == 0:
			frappe.throw(_("Cannot split Plant Batch as there is no untracked count."))

		if self.untracked_count < int(split_count):
			frappe.throw(_("The split count ({0}) should be less or equal to the untracked quantity ({1})").format(split_count, self.untracked_count))

		plant_batch = frappe.get_doc(
			dict(
				doctype='Plant Batch',
				title=new_plant_batch_id,
				strain=self.strain,
				start_date=getdate(nowdate()),
				untracked_count=split_count,
				location=self.location
			)
		).insert()
		self.untracked_count -= int(split_count)
		self.save()

		return plant_batch.name


def get_coordinates(doc):
	return ast.literal_eval(doc.location).get('features')[0].get('geometry').get('coordinates')


def get_geometry_type(doc):
	return ast.literal_eval(doc.location).get('features')[0].get('geometry').get('type')

def validate_quantities(doc, destroy_count):
		if doc.untracked_count == 0:
			frappe.throw(_("The plant batch must have an untracked count."))

		if int(destroy_count) <= 0 :
			frappe.throw(_("Destroy count cannot be less than or equal to 0."))

		if doc.untracked_count < int(destroy_count):
			frappe.throw(_("The Destroy Count ({0}) should be less than or equal to the untracked count ({1})").format(destroy_count,doc.untracked_count))


def is_in_location(point, vs):
	x, y = point
	inside = False

	j = len(vs) - 1
	i = 0

	while i < len(vs):
		xi, yi = vs[i]
		xj, yj = vs[j]

		intersect = ((yi > y) != (yj > y)) and (
			x < (xj - xi) * (y - yi) / (yj - yi) + xi)

		if intersect:
			inside = not inside

		i = j
		j += 1

	return inside


@frappe.whitelist()
def make_plant(source_name, target_doc=None):
	target_doc = get_mapped_doc("Plant Batch", source_name,
		{"Plant Batch": {
			"doctype": "Plant",
			"field_map": {
				"name": "plant_batch"
			}
		}}, target_doc)

	return target_doc


@frappe.whitelist()
def make_harvest(source_name, target_doc=None):
	def update_plant(source, target):
		target.append("plants", {
			"plant_tag": source.plant_tag,
			"plant_batch": source.name,
			"strain": source.strain,
			"actual_date": today()
		})

	target_doc = get_mapped_doc("Plant Batch", source_name,
		{"Plant Batch": {
			"doctype": "Harvest",
			"field_map": {
				"location": "harvest_location"
			}
		}}, target_doc, update_plant)

	return target_doc

def execute_bloomtrace_integration_request():
	frappe_client = get_bloomtrace_client()
	if not frappe_client:
		return

	pending_requests = frappe.get_all("Integration Request",
		filters={"status": ["IN", ["Queued", "Failed"]], "reference_doctype": "Plant Batch", "integration_request_service": "BloomTrace"},
		order_by="creation ASC",
		limit=50)

	for request in pending_requests:
		integration_request = frappe.get_doc("Integration Request", request.name)
		plant_batch = frappe.get_doc("Plant Batch", integration_request.reference_docname)
		bloomtrace_plant_batch = frappe_client.get_doc("Plant Batch", integration_request.reference_docname)
		try:
			if not bloomtrace_plant_batch:
				insert_plant_batch(plant_batch, frappe_client)
			else:
				update_plant_batch(plant_batch, frappe_client)
			integration_request.error = ""
			integration_request.status = "Completed"
			integration_request.save(ignore_permissions=True)
		except Exception as e:
			integration_request.error = cstr(frappe.get_traceback())
			integration_request.status = "Failed"
			integration_request.save(ignore_permissions=True)

def insert_plant_batch(plant_batch, frappe_client):
	bloomtrace_plant_batch = make_plant_batch(plant_batch)
	frappe_client.insert(bloomtrace_plant_batch)

def update_plant_batch(plant_batch, frappe_client):
	bloomtrace_plant_batch = make_plant_batch(plant_batch)
	bloomtrace_plant_batch.update({
		"name": plant_batch.name
	})
	frappe_client.update(bloomtrace_plant_batch)

def make_plant_batch(plant_batch):
	bloomtrace_plant_batch_dict = {
		"doctype": "Plant Batch",
		"bloomstack_company": plant_batch.company,
		"plant_batch":plant_batch.title,
		"type": plant_batch.cycle_type,
		"strain_name": plant_batch.strain,
		"location_name": plant_batch.location,
		"planted_date": plant_batch.start_date,
		"growth_date": plant_batch.growth_date,
		"untracked_count": plant_batch.untracked_count,
		"tracked_count": plant_batch.tracked_count,
		"growth_phase": plant_batch.growth_phase,
		"packaged_count": plant_batch.packaged_count,
		"harvested_count": plant_batch.harvested_count,
		"destroyed_count": plant_batch.destroyed_count
	}
	return bloomtrace_plant_batch_dict