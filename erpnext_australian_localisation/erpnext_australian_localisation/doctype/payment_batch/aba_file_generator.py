from datetime import datetime
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

ABA_ACCOUNT_WIDTH = 9


def aba_account_field(bank_account_no, owner):
	"""Refuse account numbers that cannot fit the ABA detail record."""
	if len(bank_account_no) > ABA_ACCOUNT_WIDTH:
		frappe.throw(
			_("Bank account number for {0} has {1} digits; an ABA file allows at most {2}.").format(
				owner, len(bank_account_no), ABA_ACCOUNT_WIDTH
			)
		)
	return bank_account_no.rjust(ABA_ACCOUNT_WIDTH)


def aba_amount_cents(amount):
	"""Return positive cents that fit the ten-digit ABA amount field."""
	try:
		value = Decimal(str(amount))
	except InvalidOperation:
		frappe.throw(_("ABA amounts must be valid positive numbers."))
	if not value.is_finite() or not 0 < value <= Decimal("99999999.99"):
		frappe.throw(_("ABA amounts must be positive and no greater than 99,999,999.99."))
	cents = round(value * 100)
	if cents == 0:
		frappe.throw(_("ABA amounts must round to at least one cent."))
	return cents


@frappe.whitelist()
def generate_aba_file(payment_batch):
	amounts = [aba_amount_cents(row.amount) for row in payment_batch.payment_created]
	total_cents = aba_amount_cents(payment_batch.total_paid_amount)
	if sum(amounts) != total_cents:
		frappe.throw(_("ABA payment amounts do not reconcile to the batch total."))
	if len(amounts) > 999999:
		frappe.throw(_("An ABA file allows at most 999,999 payments."))
	bank_account = frappe.db.get_value(
		"Bank Account",
		payment_batch.bank_account,
		["company", "apca_number", "bank_account_no", "branch_code", "fi_abbr"],
		as_dict=True,
	)
	posting_date = datetime.strptime(payment_batch.posting_date, "%Y-%m-%d")

	# header
	content = "0"
	content += " " * 17
	content += "01"
	if bank_account.fi_abbr:
		content += bank_account.fi_abbr
	else:
		frappe.throw(
			_("Financial Institution Abbreviation not found for Bank Account {0}").format(
				payment_batch.bank_account
			)
		)
	content += " " * 7
	content += bank_account.company[0:26].ljust(26)
	if bank_account.apca_number:
		content += bank_account.apca_number[0:6].rjust(6, "0")
	else:
		frappe.throw(_("APCA Number not found for Bank Account {0}").format(payment_batch.bank_account))
	content += bank_account.company[0:12].ljust(12)
	content += posting_date.strftime("%d%m%y")
	content += " " * 40
	content += "\n"

	# for all receiver
	for payment_entry, amount_cents in zip(payment_batch.payment_created, amounts, strict=True):
		reference_no = frappe.db.get_value(
			"Payment Entry",
			payment_entry.payment_entry,
			"reference_no",
		)
		party_account_details = frappe.db.get_value(
			payment_entry.party_type,
			payment_entry.party,
			["bank_account_no", "branch_code", payment_entry.party_type.lower() + "_name"],
			as_dict=True,
		)
		content += "1"

		if party_account_details.branch_code:
			content += party_account_details.branch_code[0:7].ljust(7)
		else:
			frappe.throw(
				_("Branch code not found for {0} {1}").format(payment_entry.party_type, payment_entry.party)
			)

		if party_account_details.bank_account_no:
			content += aba_account_field(
				party_account_details.bank_account_no,
				f"{payment_entry.party_type} {payment_entry.party}",
			)
		else:
			frappe.throw(
				_("Bank account number not found for {0} {1}").format(
					payment_entry.party_type, payment_entry.party
				)
			)

		content += " "
		content += "50"
		content += str(amount_cents).rjust(10, "0")
		content += party_account_details.get(payment_entry.party_type.lower() + "_name")[0:32].ljust(32)
		content += reference_no[0:18].ljust(18)

		if bank_account.branch_code:
			content += bank_account.branch_code[0:7].ljust(7)
		else:
			frappe.throw(_("Branch code not found for Bank Account {0}").format(payment_batch.bank_account))

		if bank_account.bank_account_no:
			content += aba_account_field(bank_account.bank_account_no, payment_batch.bank_account)
		else:
			frappe.throw(
				_("Bank account number not found for Bank Account {0}").format(payment_batch.bank_account)
			)
		content += bank_account.company[0:16].ljust(16)
		content += "0" * 8
		content += "\n"

	# footer
	content += "7"
	content += "999-999"
	content += " " * 12
	content += str(total_cents).rjust(10, "0")
	content += str(total_cents).rjust(10, "0")
	content += "0" * 10
	content += " " * 24
	content += str(len(payment_batch.payment_created)).rjust(6, "0")
	content += " " * 40
	content += "\n"

	return content
