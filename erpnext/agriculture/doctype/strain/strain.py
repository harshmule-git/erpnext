# -*- coding: utf-8 -*-
# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
import json
import requests
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint
from requests.exceptions import HTTPError
from datetime import datetime

class Strain(Document):
	def validate(self):
		self.validate_strain_tasks()
		self.update_to_bloomtrace()

	def validate_strain_tasks(self):
		for task in self.cultivation_task:
			if task.start_day > task.end_day:
				frappe.throw(_("Start day is greater than end day in task '{0}'").format(task.task_name))

		# Verify that the strain period is correct
		max_strain_period = max([task.end_day for task in self.cultivation_task], default=0)
		self.period = max(cint(self.period), max_strain_period)

		# Sort the strain tasks based on start days,
		# maintaining the order for same-day tasks
		self.cultivation_task.sort(key=lambda task: task.start_day)
	
	def update_to_bloomtrace(self):
		"""
		Create a new strain on Bloomtrace via Bloomstack.
		Args: 
		
		Returns: 
			Successful Message to the User
		"""
		now = datetime.now()
		try:
			request_data = {
				"site_url": "manufacturing.bloomstack.io",
				"customer_name": "Bloomstack",
				"company_name": "Bloomstack India",
				"license_number": "A12-0000015-LIC",
				"Hello": "World",
				"rest_method": "POST",
				"environment": "sandbox",
				"rest_operation": "Strains Create",
				"doctype": "Strain",
				"doctype_data": {
					"name": self.strain_name,
					"owner": "neil@bloomstack.com",
					"creation": now.strftime("%Y-%m-%dT%H:%M%z"),
					"modified": now.strftime("%Y-%m-%dT%H:%M%z"),
					"modified_by": "neil@bloomstack.com",
					"parent": None,
					"parentfield": None,
					"parenttype": None,
					"idx": 0,
					"docstatus": 0,
					"company": "Bloom91",
					"strain_name": "Ear Strain 1010",
					"indica_percentage": self.indica_percentage,
					"sativa_percentage": self.sativa_percentage,
					"strain_id": None,
					"target_warehouse": self.target_warehouse,
					"default_location": self.default_location,
					"period": self.period,
					"plant_spacing": self.plant_spacing if self.plant_spacing else 0.0,
					"plant_spacing_uom": self.plant_spacing_uom,
					"type": self.type,
					"category": self.category,
					"planting_uom": self.planting_uom,
					"yield_uom": self.yield_uom,
					"doctype": "Strain",
					"cultivation_task": list(x.as_dict() for x in self.cultivation_task) if self.cultivation_task else [],
					"materials_required": list(x.as_dict() for x in self.materials_required) if self.materials_required else [],
					"produced_items": list(x.as_dict() for x in self.produced_items) if self.produced_items else [],
					"byproducts": list(x.as_dict() for x in self.byproducts) if self.byproducts else []
				}
			}
			# create a strain on the bloomtrace
			bloomtrace_response = requests.post('https://bl2qu9obqb.execute-api.ap-south-1.amazonaws.com/dev/doctype/createstrain', json=request_data)
			# check if response coming from requests is successful or not.
			if bloomtrace_response.status_code in [200, 201]:
				response = json.loads(bloomtrace_response.content)
				frappe.msgprint(response.get('message'))
			else:
				response_message = {
					400: "An error has occurred while executing request for METRC",
					401: "Invalid or no authentication provided for METRC",
					403: "The authenticated user does not have access to the requested resource for METRC",
					404: "The requested resource could not be found (incorrect or invalid URI) for METRC",
					429: "The limit of API calls allowed has been exceeded for METRC. Please pace the usage rate of API more apart",
					500: "METRC Internal server error"
				}
				frappe.msgprint(response_message.get(bloomtrace_response.status_code))
		except HTTPError as http_err:
			frappe.msgprint("HTTP error occurred: {0}".format(http_err))
		except Exception as err:
			frappe.msgprint("Failed to create strain on metrc: {0}".format(err))


@frappe.whitelist()
def get_item_details(item_code):
	item = frappe.get_doc('Item', item_code)
	return {"uom": item.stock_uom, "rate": item.valuation_rate}


@frappe.whitelist()
def make_plant_batch(source_name, target_doc=None):
	target_doc = get_mapped_doc("Strain", source_name,
		{"Strain": {
			"doctype": "Plant Batch",
			"field_map": {
				"default_location": "location"
			}
		}}, target_doc)

	return target_doc
