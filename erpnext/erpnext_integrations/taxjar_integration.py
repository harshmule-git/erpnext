import traceback

import pycountry
import taxjar

import frappe
from erpnext import get_default_company
from frappe import _
from frappe.contacts.doctype.address.address import get_company_address


TAXJAR_CREATE_TRANSACTIONS = frappe.db.get_single_value("TaxJar Settings", "taxjar_create_transactions")
TAXJAR_CALCULATE_TAX = frappe.db.get_single_value("TaxJar Settings", "taxjar_calculate_tax")
TAXJAR_ENABLED = frappe.db.get_single_value("TaxJar Settings", "enabled")
SUPPORTED_COUNTRY_CODES = ["AT", "AU", "BE", "BG", "CA", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
	"FR", "GB", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
	"SE", "SI", "SK", "US"]

class AddressError(frappe.ValidationError): pass

def get_client():
	if not TAXJAR_ENABLED:
		return

	taxjar_settings = frappe.get_single("TaxJar Settings")

	if not taxjar_settings.is_sandbox:
		api_key = taxjar_settings.get_password("api_key")
		api_url = taxjar.DEFAULT_API_URL
	else:
		api_key = taxjar_settings.get_password("sandbox_api_key")
		api_url = taxjar.SANDBOX_API_URL

	if api_key and api_url:
		client = taxjar.Client(api_key=api_key, api_url=api_url)
		client.set_api_config('headers', {
			'x-api-version': '2020-08-07'
		})
		return client

def create_transaction(doc, method):
	"""Create an order transaction in TaxJar"""

	if not TAXJAR_CREATE_TRANSACTIONS:
		return

	client = get_client()

	if not client:
		return

	account_heads = get_account_heads(doc.company)
	TAX_ACCOUNT_HEAD = account_heads['TAX_ACCOUNT_HEAD']

	sales_tax = sum([tax.tax_amount for tax in doc.taxes if tax.account_head == TAX_ACCOUNT_HEAD])

	if not sales_tax:
		return

	tax_dict = get_tax_data(doc)

	if not tax_dict:
		return

	tax_dict['transaction_id'] = doc.name
	tax_dict['transaction_date'] = frappe.utils.today()
	tax_dict['sales_tax'] = sales_tax
	tax_dict['amount'] = doc.net_total + tax_dict['shipping']

	try:
		client.create_order(tax_dict)
	except taxjar.exceptions.TaxJarResponseError as err:
		error_msg = """There seems to be an error in Address<br><br>{0}""".format(sanitize_error_response(err))
		frappe.throw(_(error_msg), AddressError, _("Address Error"))
	except Exception as ex:
		print(traceback.format_exc(ex))


def delete_transaction(doc, method):
	"""Delete an existing TaxJar order transaction"""

	if not TAXJAR_CREATE_TRANSACTIONS:
		return

	client = get_client()

	if not client:
		return

	client.delete_order(doc.name)


def get_tax_data(doc):
	from_address = get_company_address_details(doc)
	from_shipping_state = from_address.get("state")
	from_country_code = frappe.db.get_value("Country", from_address.country, "code")
	from_country_code = from_country_code.upper()

	to_address = get_shipping_address_details(doc)
	to_shipping_state = to_address.get("state")
	to_country_code = frappe.db.get_value("Country", to_address.country, "code")
	to_country_code = to_country_code.upper()

	if to_country_code not in SUPPORTED_COUNTRY_CODES:
		return

	account_heads = get_account_heads(doc.company)
	SHIP_ACCOUNT_HEAD = account_heads['SHIP_ACCOUNT_HEAD']

	shipping = sum([tax.tax_amount for tax in doc.taxes if tax.account_head == SHIP_ACCOUNT_HEAD])

	if to_shipping_state is not None:
		to_shipping_state = get_iso_3166_2_state_code(to_address)

	line_items = get_line_items(doc)
	#warning: we are comenting line_items for now. once discount approach stable will enable line_items.
	tax_dict = {
		'from_country': from_country_code,
		'from_zip': from_address.pincode,
		'from_state': from_shipping_state,
		'from_city': from_address.city,
		'from_street': from_address.address_line1,
		'to_country': to_country_code,
		'to_zip': to_address.pincode,
		'to_city': to_address.city,
		'to_street': to_address.address_line1,
		'to_state': to_shipping_state,
		'shipping': shipping,
		'amount': doc.net_total,
		#'line_items': line_items,
		'plugin': '[bloomstack]'
	}

	return tax_dict


def set_sales_tax(doc, method):
	if not TAXJAR_CALCULATE_TAX:
		return

	if not doc.items:
		return

	# if doctype is Quotation and it is created for lead set sales_tax_exempted to zero
	if doc.doctype == "Quotation" and doc.quotation_to == "Lead":
		sales_tax_exempted = hasattr(doc, "exempt_from_sales_tax") and doc.exempt_from_sales_tax \
			or frappe.db.has_column("Lead", "exempt_from_sales_tax") and frappe.db.get_value("Lead", doc.party_name, "exempt_from_sales_tax")
	# if the party is exempt from sales tax, then set all tax account heads to zero
	else:
		sales_tax_exempted = hasattr(doc, "exempt_from_sales_tax") and doc.exempt_from_sales_tax \
			or frappe.db.has_column("Customer", "exempt_from_sales_tax") and frappe.db.get_value("Customer", doc.customer, "exempt_from_sales_tax")

	account_heads = get_account_heads(doc.company)
	TAX_ACCOUNT_HEAD = account_heads['TAX_ACCOUNT_HEAD']

	if not TAX_ACCOUNT_HEAD:
		frappe.throw(_("Please add accounts in taxjar setting for company " + doc.company))

	if sales_tax_exempted:
		for tax in doc.taxes:
			if tax.account_head == TAX_ACCOUNT_HEAD:
				tax.tax_amount = 0
				break

		doc.run_method("calculate_taxes_and_totals")
		return

	tax_dict = get_tax_data(doc)

	if not tax_dict:
		# Remove existing tax rows if address is changed from a taxable state/country
		setattr(doc, "taxes", [tax for tax in doc.taxes if tax.account_head != TAX_ACCOUNT_HEAD])
		return

	tax_dict['nexus_address'] = [{
		'id': get_company_address_details(doc).name,
		'country': tax_dict.get("from_country"),
		'zip': tax_dict.get("from_zip"),
		'state': tax_dict.get("from_state"),
		'city': tax_dict.get("from_city"),
		'street': tax_dict.get("from_street")
	}]

	tax_data = validate_tax_request(tax_dict)

	if tax_data is not None:
		if not tax_data.amount_to_collect:
			setattr(doc, "taxes", [tax for tax in doc.taxes if tax.account_head != TAX_ACCOUNT_HEAD])
		elif tax_data.amount_to_collect > 0:
			# Loop through tax rows for existing Sales Tax entry
			# If none are found, add a row with the tax amount
			for tax in doc.taxes:
				if tax.account_head == TAX_ACCOUNT_HEAD:
					tax.tax_amount = tax_data.amount_to_collect

					doc.run_method("calculate_taxes_and_totals")
					break
			else:
				doc.append("taxes", {
					"category": "Total",
					"add_deduct_tax": "Add",
					"charge_type": "Actual",
					"description": "Sales Tax",
					"account_head": TAX_ACCOUNT_HEAD,
					"tax_amount": tax_data.amount_to_collect
				})

			doc.run_method("calculate_taxes_and_totals")


def validate_tax_request(tax_dict):
	"""Return the sales tax that should be collected for a given order."""

	client = get_client()

	if not client:
		return

	try:
		tax_data = client.tax_for_order(tax_dict)
	except taxjar.exceptions.TaxJarResponseError as err:
		error_msg = """There seems to be an error in Address<br><br>{0}""".format(sanitize_error_response(err))
		frappe.throw(_(error_msg), AddressError, _("Address Error"))
	else:
		return tax_data


def get_company_address_details(doc):
	"""Return default company address details"""

	company_address = get_company_address(doc.company).company_address

	if not company_address:
		frappe.throw(_("Please set a default company address"))

	company_address = frappe.get_doc("Address", company_address)
	return company_address


def get_shipping_address_details(doc):
	"""Return customer shipping address details"""

	if doc.shipping_address_name:
		shipping_address = frappe.get_doc("Address", doc.shipping_address_name)
	else:
		shipping_address = get_company_address_details(doc)

	return shipping_address


def get_iso_3166_2_state_code(address):
	if not address.get("state"):
		return ""

	country_code = frappe.db.get_value("Country", address.get("country"), "code")

	error_message = _("""{0} is not a valid state! Check for typos or enter the ISO code for your state.""").format(address.get("state"))
	state = address.get("state").upper().strip()

	# The max length for ISO state codes is 3, excluding the country code
	if len(state) <= 3:
		# PyCountry returns state code as {country_code}-{state-code} (e.g. US-FL)
		address_state = (country_code + "-" + state).upper()

		states = pycountry.subdivisions.get(country_code=country_code.upper())
		states = [pystate.code for pystate in states]

		if address_state in states:
			return state

		frappe.throw(_(error_message))
	else:
		try:
			lookup_state = pycountry.subdivisions.lookup(state)
		except LookupError:
			frappe.throw(_(error_message))
		else:
			return lookup_state.code.split('-')[1]


def sanitize_error_response(response):
	response = response.full_response.get("detail")
	response = response.replace("_", " ")

	sanitized_responses = {
		"to zip": "Zipcode",
		"to city": "City",
		"to state": "State",
		"to country": "Country"
	}

	for k, v in sanitized_responses.items():
		response = response.replace(k, v)

	return response


def get_line_items(doc):
	if not doc.items:
		return

	discout_percent = 0.0

	if doc.discount_amount:
		discout_percent = ((doc.discount_amount / doc.total) * 100)

	line_items = []
	for item in doc.items:
		if discout_percent:
			item.discount_amount = ((item.price_list_rate * discout_percent) / 100)
		line_item = {
			'id': item.name,
			'description': item.item_name,
			'quantity': item.qty,
			'unit_price': item.price_list_rate,
			'discount': round(item.discount_amount * item.qty)
		}
		line_items.append(line_item)

	return line_items


def get_account_heads(current_company):
	company_account_heads = frappe.get_all("TaxJar Company", filters={"company_name": current_company}, fields=["tax_account_head","shipping_account_head"])
	account_heads = dict()
	if not company_account_heads:
		account_heads['TAX_ACCOUNT_HEAD'] = None
		account_heads['SHIP_ACCOUNT_HEAD'] = None
	else:
		account_heads['TAX_ACCOUNT_HEAD'] = company_account_heads[0]['tax_account_head']
		account_heads['SHIP_ACCOUNT_HEAD'] = company_account_heads[0]['shipping_account_head']

	return account_heads