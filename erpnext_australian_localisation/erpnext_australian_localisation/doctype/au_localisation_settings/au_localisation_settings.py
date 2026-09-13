# Copyright (c) 2025, frappe.dev@arus.co.in and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AULocalisationSettings(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if not previous:
			return
		current = {row.company: row for row in self.bas_reporting_period}
		for old in previous.bas_reporting_period:
			row = current.get(old.company)
			if row is None:
				if frappe.db.exists("AU BAS Report", {"company": old.company}):
					frappe.throw(_("Cannot remove BAS settings for a company with BAS reports."))
			elif (row.reporting_period, row.reporting_method) != (old.reporting_period, old.reporting_method):
				if frappe.db.exists("AU BAS Report", {"company": old.company, "docstatus": 0}):
					frappe.throw(_("Cannot change BAS settings while the company has a draft BAS report."))

	def on_update(self):
		frappe.cache.delete_keys("bootinfo")


@frappe.whitelist()
def is_draft(company):
	bas_report = frappe.get_list("AU BAS Report", filters={"docstatus": 0, "company": company})
	if bas_report:
		return True
	return False


def get_au_email_templates():
	from erpnext_australian_localisation.setup.install_fixtures import get_default_email_templates

	return [record["name"] for record in get_default_email_templates()]


@frappe.whitelist()
def get_disabled_email_templates():
	return frappe.get_all(
		"Email Template",
		filters={"name": ("in", get_au_email_templates()), "enabled": 0},
		pluck="name",
	)


@frappe.whitelist()
def enable_email_templates():
	frappe.get_doc("AU Localisation Settings").check_permission("write")
	enabled = get_disabled_email_templates()
	for template in enabled:
		frappe.db.set_value("Email Template", template, "enabled", 1)

	return enabled
