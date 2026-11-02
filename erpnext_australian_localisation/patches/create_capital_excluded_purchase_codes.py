import frappe

from erpnext_australian_localisation.setup.install_fixtures import get_au_bas_label_setup, get_au_tax_codes

NEW_CODES = {"AUPCAINPTAX", "AUPCAPVTUSE"}


def execute():
	for record in get_au_tax_codes():
		if record["tax_code"] in NEW_CODES and not frappe.db.exists("AU Tax Code", record["tax_code"]):
			frappe.get_doc(record).insert(ignore_permissions=True)
	for record in get_au_bas_label_setup():
		if record["tax_code"] in NEW_CODES and not frappe.db.exists(
			"AU BAS Label Setup", {key: value for key, value in record.items() if key != "doctype"}
		):
			frappe.get_doc(record).insert(ignore_permissions=True)
