# Copyright (c) 2026, frappe.dev@arus.co.in and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AUBankStatementFormat(Document):
	def validate(self):
		validate_mapping(self)


def validate_mapping(doc):
	if doc.credit_debit_mapping not in ("Single credit&debit", "Combined credit&debit"):
		frappe.throw(_("Select a supported credit and debit mapping."))
	columns = set()
	for row in doc.mapping_fields:
		if not row.erpnext_column or not row.bank_statement_column or not row.bank_statement_column.strip():
			frappe.throw(_("Every bank statement mapping row needs both column values."))
		if row.erpnext_column in columns:
			frappe.throw(_("Each ERPNext column can be mapped only once."))
		columns.add(row.erpnext_column)
	if not {"Date", "Description"} <= columns:
		frappe.throw(_("Map the Date and Description columns before importing."))
	amount_columns = {"Deposit", "Withdrawal"}
	if (
		doc.credit_debit_mapping == "Single credit&debit" and not amount_columns <= columns
	) or not amount_columns & columns:
		frappe.throw(_("Map the deposit and withdrawal columns for the selected format."))
