# Copyright (c) 2025, frappe.dev@arus.co.in and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AUSimplerBASReportSetup(Document):
	def validate(self):
		if self.account_1a and self.account_1a == self.account_1b:
			frappe.throw(_("1A and 1B can't be reported in the same account"))
		accounts = [(row.account, "Income Account") for row in self.accounts_g1]
		accounts.extend([(self.account_1a, "Tax"), (self.account_1b, "Tax")])
		for name, expected_type in accounts:
			# Company setup may save an incomplete configuration before accounts exist.
			# Required fields are enforced on ordinary saves by the DocType schema.
			if not name:
				continue
			account = frappe.db.get_value("Account", name, ["company", "account_type"], as_dict=True)
			if not account or account.company != self.company or account.account_type != expected_type:
				frappe.throw(
					_("Account {0} must belong to {1} and have type {2}.").format(
						name, self.company, expected_type
					)
				)
