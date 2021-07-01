# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe, erpnext
from frappe import _
from frappe.utils import flt, cstr
from frappe.model.meta import get_field_precision
from frappe.utils.xlsxutils import handle_html
from erpnext.accounts.report.sales_register.sales_register import get_mode_of_payments

def execute(filters=None):
	return _execute(filters)

def _execute(filters=None, additional_table_columns=None, additional_query_columns=None):
	if not filters: filters = {}
	columns = get_columns(additional_table_columns, filters)

	company_currency = frappe.get_cached_value('Company',  filters.get("company"),  "default_currency")

	item_list = get_items(filters, additional_query_columns)

	mode_of_payments = get_mode_of_payments(set([d.parent for d in item_list]))
	so_dn_map = get_delivery_notes_against_sales_order(item_list)

	data = []
	total_row_map = {}
	skip_total_row = 0
	prev_group_by_value = ''

	if filters.get('group_by'):
		grand_total = get_grand_total(filters, 'Sales Invoice')

	for d in item_list:
		delivery_note = None
		if d.delivery_note:
			delivery_note = d.delivery_note
		elif d.so_detail:
			delivery_note = ", ".join(so_dn_map.get(d.so_detail, []))

		if not delivery_note and d.update_stock:
			delivery_note = d.parent

		row = {
			'posting_date': d.posting_date,			
			'item_name': d.item_name,
			'customer': d.customer,
		}

		if additional_query_columns:
			for col in additional_query_columns:
				row.update({
					col: d.get(col)
				})

		row.update({
			'territory': d.territory,
			'stock_qty': d.stock_qty,
			'stock_uom': d.stock_uom
		})

		if d.stock_uom != d.uom and d.stock_qty:
			row.update({
				'rate': (d.base_net_rate * d.qty)/d.stock_qty,
				'amount': d.base_net_amount
			})
		else:
			row.update({
				'rate': d.base_net_rate,
				'amount': d.base_net_amount
			})
		
		row.update({
			'batch_no': d.batch_no,
			'package_tag': d.package_tag,
			'sales_order': d.sales_order,
		})


		if filters.get('group_by'):
			row.update({'percent_gt': flt(row['total']/grand_total) * 100})
			group_by_field, subtotal_display_field = get_group_by_and_display_fields(filters)
			data, prev_group_by_value = add_total_row(data, filters, prev_group_by_value, d, total_row_map,
				group_by_field, subtotal_display_field, grand_total)
			add_sub_total_row(row, total_row_map, d.get(group_by_field, ''))

		data.append(row)

	if filters.get('group_by') and item_list:
		total_row = total_row_map.get(prev_group_by_value or d.get('item_name'))
		total_row['percent_gt'] = flt(total_row['total']/grand_total * 100)
		data.append(total_row)
		data.append({})
		add_sub_total_row(total_row, total_row_map, 'total_row')
		data.append(total_row_map.get('total_row'))
		skip_total_row = 1

	return columns, data, None, None, None, skip_total_row

def get_columns(additional_table_columns, filters):
	columns = []

	columns.extend([
		{
			'label': _('Transaction Date'),
			'fieldname': 'posting_date',
			'fieldtype': 'Date',
			'width': 120
		}
	])

	if filters.get('group_by') != ('Item'):
		columns.extend(
			[
				{
					'label': _('Item Name'),
					'fieldname': 'item_name',
					'fieldtype': 'Data',
					'width': 120
				}
			]
		)


	if filters.get('group_by') not in ('Customer', 'Customer Group'):
		columns.extend([
			{
				'label': _('Customer'),
				'fieldname': 'customer',
				'fieldtype': 'Link',
				'options': 'Customer',
				'width': 120
			}
		])

	if additional_table_columns:
		columns += additional_table_columns


	columns.extend([
		{
			'label': _("Territory"),
			'fieldname': 'territory',
			'fieldtype': 'Link',
			'options': 'Territory',
			'width': 100
		}
	])


	columns += [
		{
			'label': _('Ouantity Sold'),
			'fieldname': 'stock_qty',
			'fieldtype': 'Float',
			'width': 100
		},
		{
			'label': _('Quantity UOM'),
			'fieldname': 'stock_uom',
			'fieldtype': 'Link',
			'options': 'UOM',
			'width': 110
		},
		{
			'label': _('Rate'),
			'fieldname': 'rate',
			'fieldtype': 'Float',
			'options': 'currency',
			'width': 100
		},
		{
			'label': _('Total Amount'),
			'fieldname': 'amount',
			'fieldtype': 'Currency',
			'options': 'currency',
			'width': 100
		},
		{
			'label': _('Batch Number'),
			'fieldname': 'batch_no',
			'fieldtype': 'Link',
			'options': 'Batch',
			'width': 100
		},
		{
			'label': _('Package Tag'),
			'fieldname': 'package_tag',
			'fieldtype': 'Link',
			'options': 'Package Tag',
			'width': 100
		},
		{
			'label': _('Sales Order'),
			'fieldname': 'sales_order',
			'fieldtype': 'Link',
			'options': 'Sales Order',
			'width': 100
		},
	]

	if filters.get('group_by'):
		columns.append({
			'label': _('% Of Grand Total'),
			'fieldname': 'percent_gt',
			'fieldtype': 'Float',
			'width': 80
		})

	return columns

def get_conditions(filters):
	conditions = ""

	for opts in (("company", " and company=%(company)s"),
		("customer", " and `tabSales Invoice`.customer = %(customer)s"),
		("item_code", " and `tabSales Invoice Item`.item_code = %(item_code)s"),
		("start_date", " and `tabSales Invoice`.posting_date>=%(start_date)s"),
		("end_date", " and `tabSales Invoice`.posting_date<=%(end_date)s")):
			if filters.get(opts[0]):
				conditions += opts[1]

	if filters.get("mode_of_payment"):
		conditions += """ and exists(select name from `tabSales Invoice Payment`
			where parent=`tabSales Invoice`.name
				and ifnull(`tabSales Invoice Payment`.mode_of_payment, '') = %(mode_of_payment)s)"""
	
	if filters.get("item_name"):
		conditions += """and ifnull(`tabSales Invoice Item`.item_code, '') = %(item_name)s"""

	if filters.get("warehouse"):
		conditions +=  """and ifnull(`tabSales Invoice Item`.warehouse, '') = %(warehouse)s"""


	if filters.get("brand"):
		conditions +=  """and ifnull(`tabSales Invoice Item`.brand, '') = %(brand)s"""

	if filters.get("item_group"):
		conditions +=  """and ifnull(`tabSales Invoice Item`.item_group, '') = %(item_group)s"""

	if not filters.get("group_by"):
		conditions += "ORDER BY `tabSales Invoice`.posting_date desc, `tabSales Invoice Item`.item_group desc"
	else:
		conditions += get_group_by_conditions(filters, 'Sales Invoice')

	return conditions

def get_group_by_conditions(filters, doctype):
	if filters.get("group_by") == 'Item':
		return "ORDER BY `tab{0} Item`.`item_code`".format(doctype)
	elif filters.get("group_by") in ('Customer'):
		return "ORDER BY `tab{0}`.{1}".format(doctype, frappe.scrub(filters.get('group_by')))

def get_items(filters, additional_query_columns):
	conditions = get_conditions(filters)

	if additional_query_columns:
		additional_query_columns = ', ' + ', '.join(additional_query_columns)
	else:
		additional_query_columns = ''

	return frappe.db.sql("""
		select
			`tabSales Invoice Item`.name, `tabSales Invoice Item`.parent,
			`tabSales Invoice`.posting_date, `tabSales Invoice`.debit_to,
			`tabSales Invoice`.project, `tabSales Invoice`.customer, `tabSales Invoice`.remarks,
			`tabSales Invoice`.territory, `tabSales Invoice`.company, `tabSales Invoice`.base_net_total,
			`tabSales Invoice Item`.item_code, `tabSales Invoice Item`.item_name,
			`tabSales Invoice Item`.item_group, `tabSales Invoice Item`.description, `tabSales Invoice Item`.sales_order,
			`tabSales Invoice Item`.delivery_note, `tabSales Invoice Item`.income_account,
			`tabSales Invoice Item`.cost_center, `tabSales Invoice Item`.stock_qty,
			`tabSales Invoice Item`.stock_uom, `tabSales Invoice Item`.base_net_rate,
			`tabSales Invoice Item`.base_net_amount, `tabSales Invoice`.customer_name,
			`tabSales Invoice Item`.batch_no, `tabSales Invoice Item`.package_tag,
			`tabSales Invoice`.customer_group, `tabSales Invoice Item`.so_detail,
			`tabSales Invoice`.update_stock, `tabSales Invoice Item`.uom, `tabSales Invoice Item`.qty {0}
		from `tabSales Invoice`, `tabSales Invoice Item`
		where `tabSales Invoice`.name = `tabSales Invoice Item`.parent
			and `tabSales Invoice`.docstatus = 1 {1}
		""".format(additional_query_columns or '', conditions), filters, as_dict=1) #nosec

def get_delivery_notes_against_sales_order(item_list):
	so_dn_map = frappe._dict()
	so_item_rows = list(set([d.so_detail for d in item_list]))

	if so_item_rows:
		delivery_notes = frappe.db.sql("""
			select parent, so_detail
			from `tabDelivery Note Item`
			where docstatus=1 and so_detail in (%s)
			group by so_detail, parent
		""" % (', '.join(['%s']*len(so_item_rows))), tuple(so_item_rows), as_dict=1)

		for dn in delivery_notes:
			so_dn_map.setdefault(dn.so_detail, []).append(dn.parent)

	return so_dn_map

def get_grand_total(filters, doctype):

	return frappe.db.sql(""" SELECT
		SUM(`tab{0}`.base_grand_total)
		FROM `tab{0}`
		WHERE `tab{0}`.docstatus = 1
		and posting_date between %s and %s """.format(doctype), (filters.get('start_date'), filters.get('end_date')))[0][0] #nosec

def get_deducted_taxes():
	return frappe.db.sql_list("select name from `tabPurchase Taxes and Charges` where add_deduct_tax = 'Deduct'")

def add_total_row(data, filters, prev_group_by_value, item, total_row_map,
	group_by_field, subtotal_display_field, grand_total, tax_columns):
	if prev_group_by_value != item.get(group_by_field, ''):
		if prev_group_by_value:
			total_row = total_row_map.get(prev_group_by_value)
			data.append(total_row)
			data.append({})
			add_sub_total_row(total_row, total_row_map, 'total_row', tax_columns)

		prev_group_by_value = item.get(group_by_field, '')

		total_row_map.setdefault(item.get(group_by_field, ''), {
			subtotal_display_field: get_display_value(filters, group_by_field, item),
			'stock_qty': 0.0,
			'amount': 0.0,
			'bold': 1,
			'total_tax': 0.0,
			'total': 0.0,
			'percent_gt': 0.0
		})

		total_row_map.setdefault('total_row', {
			subtotal_display_field: "Total",
			'stock_qty': 0.0,
			'amount': 0.0,
			'bold': 1,
			'total_tax': 0.0,
			'total': 0.0,
			'percent_gt': 0.0
		})

	return data, prev_group_by_value

def get_display_value(filters, group_by_field, item):
	if filters.get('group_by') == 'Item':
		if item.get('item_code') != item.get('item_name'):
			value =  cstr(item.get('item_code')) + "<br><br>" + \
			"<span style='font-weight: normal'>" + cstr(item.get('item_name')) + "</span>"
		else:
			value =  item.get('item_code', '')
	elif filters.get('group_by') in ('Customer', 'Supplier'):
		party = frappe.scrub(filters.get('group_by'))
		if item.get(party) != item.get(party+'_name'):
			value = item.get(party) + "<br><br>" + \
			"<span style='font-weight: normal'>" + item.get(party+'_name') + "</span>"
		else:
			value =  item.get(party)
	else:
		value = item.get(group_by_field)

	return value

def get_group_by_and_display_fields(filters):
	if filters.get('group_by') == 'Item':
		group_by_field = 'item_code'
		subtotal_display_field = 'invoice'
	elif filters.get('group_by') == 'Invoice':
		group_by_field = 'parent'
		subtotal_display_field = 'item_code'
	else:
		group_by_field = frappe.scrub(filters.get('group_by'))
		subtotal_display_field = 'item_code'

	return group_by_field, subtotal_display_field

def add_sub_total_row(item, total_row_map, group_by_value, tax_columns):
	total_row = total_row_map.get(group_by_value)
	total_row['stock_qty'] += item['stock_qty']
	total_row['amount'] += item['amount']
	total_row['total_tax'] += item['total_tax']
	total_row['total'] += item['total']
	total_row['percent_gt'] += item['percent_gt']

	for tax in tax_columns:
		total_row.setdefault(frappe.scrub(tax + ' Amount'), 0.0)
		total_row[frappe.scrub(tax + ' Amount')] += flt(item[frappe.scrub(tax + ' Amount')])




