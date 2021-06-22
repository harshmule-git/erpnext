frappe.query_reports['Ordered Items To Be Delivered'] = {
    "filters": [
        {
            "fieldname":"customer",
            "label": __("Customer"),
            "fieldtype": "Data",
            "width": "80",
        },
        {
            "fieldname":"status",
            "label": __("Status"),
            "fieldtype": "Select",
            "width": "80",
            "options": "\nDraft\nOn Hold\nTo Pick\nTo Pick and Bill\nTo Deliver and Bill\nTo Bill\nTo Deliver\nCompleted\nCancelled\nClosed"
        },
        {
            "fieldname": "item_name",
            "label": __("Item Name"),
            "fieldtype": "Data",
            "width": "80"
        }
    ]
}