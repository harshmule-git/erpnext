from __future__ import unicode_literals
from frappe import _

def get_data():
	return {
		'fieldname': 'sales_order',
		'non_standard_fieldnames': {
			'Delivery Note': 'against_sales_order',
			'Journal Entry': 'reference_name',
			'Payment Entry': 'reference_name',
			'Payment Request': 'reference_name',
			'Auto Repeat': 'reference_document',
			'Production Plan': 'sales_order',
		},
		'internal_links': {
			'Quotation': ['items', 'prevdoc_docname']
		},
		'transactions': [
			{
				'label': _('Fulfillment'),
				'items': ['Sales Invoice', 'Pick List', 'Delivery Note']
			},
			{
				'label': _('Purchasing'),
				'items': ['Material Request', 'Purchase Order']
			},
			{
				'label': _('Projects'),
				'items': ['Project']
			},
			{
				'label': _('Manufacturing'),
				'items': ['Work Order']
			},
			{
				'label': _('Reference'),
				'items': ['Quotation', 'Auto Repeat', 'Production Plan']
			},
			{
				'label': _('Payment'),
				'items': ['Payment Entry', 'Payment Request', 'Journal Entry']
			},
			{
				'label': _('Serial Numbers'),
				'items': ['Serial No']
			}
		]
	}