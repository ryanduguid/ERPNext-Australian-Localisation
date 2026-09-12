import frappe
from frappe import _

from erpnext_australian_localisation.erpnext_australian_localisation.doctype.payment_batch.payment_batch import (
	_send_remittance_email,
)


@frappe.whitelist()
def check_party_email(docname: str, party_type: str):
	entry = frappe.get_doc("Payment Entry", docname)
	entry.check_permission("read")
	if party_type != entry.party_type:
		frappe.throw(_("Party type must match the Payment Entry."), frappe.PermissionError)
	get_party_email(entry, party_type)
	return True


def get_party_email(entry, party_type):
	if entry.party_type != party_type:
		frappe.throw(_("Party type must match the Payment Entry."), frappe.PermissionError)
	email = frappe.db.get_value(
		"Contact",
		{"link_doctype": party_type, "link_name": entry.party, "is_primary_contact": 1},
		"email_id",
	)
	if not email:
		action = _("Remittance Advice") if party_type == "Supplier" else _("Payment Receipt")
		frappe.throw(
			_(
				"Please set a Primary Contact with email address in the {0} master for {1} to send the {2}"
			).format(party_type, entry.party_name, action)
		)
	return email


@frappe.whitelist()
def send_payment_receipt(docname: str):
	doc = frappe.get_doc("Payment Entry", docname)
	doc.check_permission("read")
	email = get_party_email(doc, "Customer")

	template = frappe.get_cached_value(
		"AU Localisation Settings", "AU Localisation Settings", "payment_receipt_template"
	)
	if not template:
		frappe.throw(_("Please set a Payment Receipt Template in AU Localisation Settings"))

	pe_dict = doc.as_dict()

	template_data = frappe.get_attr("frappe.email.doctype.email_template.email_template.get_email_template")(
		template_name=template,
		doc=frappe.as_json(pe_dict),
	)

	if not template_data:
		frappe.throw(_("Could not render email template"))

	frappe.get_attr("frappe.core.doctype.communication.email.make")(
		doctype="Payment Entry",
		name=docname,
		recipients=email,
		subject=template_data.get("subject"),
		content=template_data.get("message"),
		send_email=1,
		print_format="Payment Receipt",
		print_letterhead=1,
		print_language="en",
		add_css=1,
	)

	return True


@frappe.whitelist()
def send_remittance_email(docname: str):
	doc = frappe.get_doc("Payment Entry", docname)
	doc.check_permission("read")
	email = get_party_email(doc, "Supplier")
	template = frappe.get_cached_value(
		"AU Localisation Settings", "AU Localisation Settings", "remittance_advice_template"
	)
	if not template:
		frappe.throw(_("Please set a Remittance Advice Template in AU Localisation Settings"))
	if email:
		_send_remittance_email(
			payment_entry=docname,
			email=email,
			template=template,
		)
	return True
