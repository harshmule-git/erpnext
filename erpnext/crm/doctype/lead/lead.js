// Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext");
cur_frm.email_field = "email_id";

erpnext.LeadController = frappe.ui.form.Controller.extend({
	setup: function () {
		this.frm.make_methods = {
			'Customer': this.make_customer,
			'Quotation': this.make_quotation,
			'Opportunity': this.make_opportunity,
			'Investor': this.make_investor
		};

		this.frm.toggle_reqd("lead_name", !this.frm.doc.organization_lead);
	},

	onload: function () {
		this.frm.set_query("customer", function (doc, cdt, cdn) {
			return { query: "erpnext.controllers.queries.customer_query" }
		});

		this.frm.set_query("lead_owner", function (doc, cdt, cdn) {
			return { query: "frappe.core.doctype.user.user.user_query" }
		});

		this.frm.set_query("contact_by", function (doc, cdt, cdn) {
			return { query: "frappe.core.doctype.user.user.user_query" }
		});
	},

	refresh: function () {
		let doc = this.frm.doc;
		erpnext.toggle_naming_series();
		frappe.dynamic_link = { doc: doc, fieldname: 'name', doctype: 'Lead' }

		if (!this.frm.is_new() && doc.__onload && !doc.__onload.is_customer) {
			this.frm.add_custom_button(__("Customer"), this.make_customer, __("Create"));
			this.frm.add_custom_button(__("Opportunity"), this.make_opportunity, __("Create"));
			this.frm.add_custom_button(__("Quotation"), this.make_quotation, __("Create"));
			this.frm.add_custom_button(__("Investor"), this.make_investor, __("Create"));
		}

		if (!this.frm.is_new()) {
			frappe.contacts.render_address_and_contact(this.frm);
		} else {
			frappe.contacts.clear_address_and_contact(this.frm);
		}
	},

	make_investor: function(){
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.lead.lead.make_investor",
			frm: cur_frm
		})
	},

	make_customer: function () {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.lead.lead.make_customer",
			frm: cur_frm
		})
	},

	make_opportunity: function () {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.lead.lead.make_opportunity",
			frm: cur_frm
		})
	},

	make_quotation: function () {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.lead.lead.make_quotation",
			frm: cur_frm
		})
	},

	organization_lead: function () {
		this.frm.toggle_reqd("lead_name", !this.frm.doc.organization_lead);
		this.frm.toggle_reqd("company_name", this.frm.doc.organization_lead);
	},

	company_name: function () {
		if (this.frm.doc.organization_lead && !this.frm.doc.lead_name) {
			this.frm.set_value("lead_name", this.frm.doc.company_name);
		}
	},

	contact_date: function () {
		if (this.frm.doc.contact_date) {
			let d = moment(this.frm.doc.contact_date);
			d.add(1, "day");
			this.frm.set_value("ends_on", d.format(frappe.defaultDatetimeFormat));
		}
	}
});

$.extend(cur_frm.cscript, new erpnext.LeadController({ frm: cur_frm }));

frappe.ui.form.on("Lead", {
	setup: (frm) => {
		frm.set_query("region", { "is_group": 1 });
	},

	onload: (frm) => {
		frm.set_query("territory", () => {
			if (frm.doc.region) {
				return {
					query: "erpnext.crm.doctype.lead.filter_territory",
					filters: {
						region: frm.doc.region
					}
				};
			}
		});

		if (!frm.doc.account_opened_date) {
			frappe.db.get_value("Customer", { "lead_name": frm.doc.name }, ["opening_date", "creation"], (r) => {
				if (r) {
					if (r.opening_date) {
						frm.set_value("account_opened_date", r.opening_date);
					} else {
						frm.set_value("account_opened_date", r.creation);
					}
				}
			});
		}
	}
});