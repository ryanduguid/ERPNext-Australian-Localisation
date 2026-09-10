from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext_australian_localisation.erpnext_australian_localisation.doctype.au_bas_report import (
	au_bas_report as bas,
)
from erpnext_australian_localisation.overrides import invoices


class Record(dict):
	def __getattr__(self, name):
		return self.get(name, 0)

	def __setattr__(self, name, value):
		self[name] = value

	def append(self, field, value):
		self.setdefault(field, []).append(value)

	def save(self, **kwargs):
		pass


class TestBASScope(TestCase):
	def test_entry_creation_refuses_non_aud_company(self):
		with (
			patch.object(frappe.db, "get_value", return_value="USD"),
			patch.object(frappe, "new_doc") as create,
			self.assertRaises(frappe.ValidationError),
		):
			invoices.create_au_bas_entries(
				"Expense Claim",
				"EXAMPLE-1",
				"Example",
				"2026-07-01",
				[{"bas_label": "1B", "account": "GST", "tax_code": "GST", "gst_offset_amount": 100}],
				["gst_offset_amount"],
			)
		create.assert_not_called()

	def test_full_aud_report_generates_and_can_be_submitted(self):
		doc = Record(
			company="Example",
			accounting_basis="Non-cash",
			reporting_method="Full reporting method",
			reporting_status="Validated",
			start_date="2026-07-01",
			end_date="2026-07-31",
		)
		doc.update({"1a": 0, "1b": 0, **{f"g{i}": 0 for i in range(1, 21)}})
		with (
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe, "get_all", return_value=["1A"]),
			patch.object(frappe, "get_list", return_value=["AUD-ENTRY"]),
			patch.object(frappe.db, "get_value", return_value="AUD"),
			patch.object(frappe, "publish_progress"),
			patch.object(frappe, "publish_realtime"),
			patch("frappe.model.mapper.get_mapped_doc", return_value=Record(gst_pay_amount=150)),
		):
			bas.get_gst("EXAMPLE-REPORT")
			self.assertEqual(doc["1a"], 150)
			self.assertEqual(doc.net_gst, 150)
			self.assertEqual(doc.bas_currency, "AUD")
			bas.AUBASReport.before_submit(doc)
			for basis in ("Cash", None):
				with self.subTest(basis=basis):
					doc.accounting_basis = basis
					with self.assertRaises(frappe.ValidationError):
						bas.AUBASReport.before_submit(doc)

	def test_gl_uses_aud_company_amounts_for_foreign_currency_accounts(self):
		entry = Record(credit=1500, debit=0, credit_in_account_currency=1000, debit_in_account_currency=0)
		with patch.object(frappe, "get_list", return_value=[entry]):
			rows = bas.get_gl_entries_for_accounts(
				"2026-07-01",
				"2026-07-31",
				"Example",
				["Sales"],
				{"fieldname": "gst_pay_basis", "obtained_by": "credit_minus_debit"},
			)
		self.assertEqual(rows[0].gst_pay_basis, 1500)

	def test_gl_uses_aud_debits_for_purchase_credits(self):
		entry = Record(credit=0, debit=150, credit_in_account_currency=0, debit_in_account_currency=100)
		with patch.object(frappe, "get_list", return_value=[entry]):
			rows = bas.get_gl_entries_for_accounts(
				"2026-07-01",
				"2026-07-31",
				"Example",
				["GST"],
				{"fieldname": "gst_offset_amount", "obtained_by": "debit_minus_credit"},
			)
		self.assertEqual(rows[0].gst_offset_amount, 150)

	def test_invoice_bas_entries_use_base_tax_and_record_aud(self):
		for doctype, amount_field in [
			("Sales Invoice", "gst_pay_amount"),
			("Purchase Invoice", "gst_offset_amount"),
		]:
			with self.subTest(doctype=doctype):
				tax = Record(
					account_head="GST",
					tax_amount_after_discount_amount=100,
					base_tax_amount_after_discount_amount=150,
				)
				doc = type(
					"Invoice",
					(),
					dict(
						doctype=doctype,
						name="EXAMPLE-1",
						company="Example",
						posting_date="2026-07-01",
						taxes_and_charges="GST",
						items=[],
						taxes=[tax],
					),
				)()
				created = []

				def new_doc(doctype):
					row = Record()
					created.append(row)
					return row

				def get_value(doctype, *args, **kwargs):
					return "AUD" if doctype == "Company" else "Tax" if doctype == "Account" else "GST"

				with (
					patch.object(frappe.db, "get_value", side_effect=get_value),
					patch.object(frappe, "get_all", return_value=[Record(bas_label="1A")]),
					patch.object(frappe, "new_doc", side_effect=new_doc),
				):
					invoices.before_submit(doc, "before_submit")
				self.assertEqual(created[0][amount_field], 150)
				self.assertEqual(created[0].get("currency"), "AUD")

	def test_report_refuses_cash_unknown_basis_and_non_aud_companies(self):
		for basis, currency in [("Cash", "AUD"), (None, "AUD"), ("Non-cash", "USD")]:
			with self.subTest(basis=basis, currency=currency):
				doc = Record(company="Example", accounting_basis=basis, reporting_method="Simpler")
				doc.update({"1a": 0, "1b": 0})
				with (
					patch.object(frappe, "get_doc", return_value=doc),
					patch.object(frappe.db, "get_value", return_value=currency),
					patch.object(frappe, "publish_realtime"),
					patch.object(frappe, "publish_progress"),
					patch.object(bas, "update_simpler_bas_report"),
					self.assertRaises(frappe.ValidationError),
				):
					bas.get_gst("EXAMPLE-REPORT")

	def test_submission_refuses_report_without_verified_aud_generation(self):
		doc = Record(company="Example", accounting_basis="Non-cash", reporting_status="Validated")
		with (
			patch.object(frappe.db, "get_value", return_value="AUD"),
			self.assertRaises(frappe.ValidationError),
		):
			bas.AUBASReport.before_submit(doc)

	def test_full_report_refuses_legacy_entries_without_currency_provenance(self):
		doc = Record(company="Example", start_date="2026-07-01", end_date="2026-07-31")
		doc.update({"1a": 0, "1b": 0, **{f"g{i}": 0 for i in range(1, 21)}})
		with (
			patch.object(frappe, "get_all", return_value=["1A"]),
			patch.object(frappe, "get_list", return_value=["LEGACY-ENTRY"]),
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe, "publish_progress"),
			patch("frappe.model.mapper.get_mapped_doc", return_value=Record()),
			self.assertRaises(frappe.ValidationError),
		):
			bas.update_full_bas_report(doc)
