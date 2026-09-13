import json
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext_australian_localisation import boot
from erpnext_australian_localisation.erpnext_australian_localisation.doctype.au_bank_statement_format import (
	au_bank_statement_format as bank_format,
)
from erpnext_australian_localisation.erpnext_australian_localisation.doctype.au_bas_report import (
	au_bas_report as bas,
)
from erpnext_australian_localisation.erpnext_australian_localisation.doctype.au_localisation_settings import (
	au_localisation_settings as settings,
)
from erpnext_australian_localisation.erpnext_australian_localisation.doctype.au_simpler_bas_report_setup import (
	au_simpler_bas_report_setup as setup,
)
from erpnext_australian_localisation.erpnext_australian_localisation.doctype.payment_batch import (
	aba_file_generator as aba,
)
from erpnext_australian_localisation.erpnext_australian_localisation.doctype.payment_batch import (
	payment_batch as batches,
)
from erpnext_australian_localisation.erpnext_australian_localisation.page.payment_proposal import (
	payment_proposal as proposal,
)
from erpnext_australian_localisation.overrides import bank_statement_import, payment_receipt


class TestReviewRegressions(TestCase):
	def test_aba_rejects_fractional_cents_without_rounding(self):
		for amount in ("0.001", "12.345", "99999999.999"):
			with self.subTest(amount=amount), self.assertRaises(frappe.ValidationError):
				aba.aba_amount_cents(amount)
		self.assertEqual(aba.aba_amount_cents("12.340"), 1234)

	def test_aba_rejects_non_aud_before_processing_amounts(self):
		for bank_currency, batch_currency in [("USD", "AUD"), ("AUD", "USD")]:
			with (
				self.subTest(bank=bank_currency, batch=batch_currency),
				patch.object(frappe.db, "get_value", return_value=frappe._dict(currency=bank_currency)),
				patch.object(aba, "aba_amount_cents") as amount,
				self.assertRaises(frappe.ValidationError),
			):
				aba.generate_aba_file(frappe._dict(bank_account="Synthetic", currency=batch_currency))
			amount.assert_not_called()

	def test_proposal_uses_bound_filters_and_document_permission_conditions(self):
		filters = {
			"party_type": "Supplier",
			"company": "x' OR 1=1 --",
			"created_by": "owner'",
			"from_due_date": "2026-01-01'",
			"to_due_date": "2026-02-01",
		}
		with (
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_doc", return_value=Mock()) as document,
			patch.object(proposal, "get_match_cond", return_value=" and owner = 'session-user'"),
			patch.object(frappe.db, "sql", return_value=[]) as query,
		):
			proposal.get_unpaid_entries(json.dumps(filters))
			sql, values = query.call_args.args
			for field in ("company", "created_by", "from_due_date", "to_due_date"):
				self.assertEqual(values[field], filters[field])
				self.assertNotIn(filters[field], sql)
			self.assertIn("per.reference_doctype = 'Purchase Invoice'", sql)
			self.assertIn("and (1=1  and owner = 'session-user')", sql)
			document.return_value.check_permission.assert_called_once_with("read")

	def test_proposal_denies_access_before_running_a_query(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError),
			patch.object(frappe.db, "sql") as query,
			self.assertRaises(frappe.PermissionError),
		):
			proposal.get_unpaid_entries(json.dumps({"party_type": "Supplier", "company": "Synthetic"}))
		query.assert_not_called()

	def test_rework_uses_the_saved_batch_and_checks_permission_first(self):
		doc = Mock()
		doc.check_permission.side_effect = frappe.PermissionError
		with (
			patch.object(frappe, "get_doc", return_value=doc) as get,
			patch.object(frappe, "new_doc") as create,
		):
			with self.assertRaises(frappe.PermissionError):
				batches.create_payment_batch_again("Saved Batch")
			get.assert_called_once_with("Payment Batch", "Saved Batch")
			create.assert_not_called()

	def test_remittance_permission_failure_prevents_any_lookup_or_send(self):
		for function, args in [
			(batches.send_remittance_email_from_pb, ("Batch",)),
			(payment_receipt.send_payment_receipt, ("Payment",)),
			(payment_receipt.send_remittance_email, ("Payment",)),
			(payment_receipt.check_party_email, ("Payment", "Supplier")),
		]:
			doc = Mock()
			doc.check_permission.side_effect = frappe.PermissionError
			with (
				self.subTest(function=function.__name__),
				patch.object(frappe, "get_doc", return_value=doc),
				patch.object(frappe.db, "get_value") as lookup,
			):
				with self.assertRaises(frappe.PermissionError):
					function(*args)
				lookup.assert_not_called()

	def test_missing_email_raises_the_intended_message_before_send(self):
		for party_type, function in [
			("Customer", payment_receipt.send_payment_receipt),
			("Supplier", payment_receipt.send_remittance_email),
		]:
			doc = Mock(party_type=party_type, party="Synthetic", party_name="Synthetic")
			with (
				patch.object(frappe, "get_doc", return_value=doc),
				patch.object(frappe.db, "get_value", return_value=None),
				patch.object(frappe, "get_attr") as send,
			):
				with self.assertRaisesRegex(frappe.ValidationError, "Primary Contact with email address"):
					function("Payment")
				send.assert_not_called()

	def test_bas_overlap_check_uses_dates_and_company_not_the_report_name(self):
		doc = frappe._dict(company="Synthetic", start_date="2026-01-01", end_date="2026-01-31")
		with patch.object(frappe.db, "exists", return_value="Renamed report") as exists:
			with self.assertRaises(frappe.ValidationError):
				bas.AUBASReport.before_insert(doc)
			self.assertEqual(
				exists.call_args.args[1],
				{
					"company": "Synthetic",
					"start_date": ["<=", "2026-01-31"],
					"end_date": [">=", "2026-01-01"],
				},
			)

	def test_bas_settings_reject_locked_changes_and_deletion(self):
		old = frappe._dict(
			company="Synthetic", reporting_period="Monthly", reporting_method="Full reporting method"
		)
		for rows in ([], [frappe._dict(old, reporting_period="Quarterly")]):
			doc = Mock(bas_reporting_period=rows)
			doc.get_doc_before_save.return_value = frappe._dict(bas_reporting_period=[old])
			with (
				patch.object(frappe.db, "exists", return_value="Report"),
				self.assertRaises(frappe.ValidationError),
			):
				settings.AULocalisationSettings.validate(doc)

	def test_simpler_bas_accounts_must_match_company_and_account_type(self):
		doc = frappe._dict(
			company="Synthetic", accounts_g1=[], account_1a="Sales GST", account_1b="Purchase GST"
		)
		for company, account_type in [("Other", "Tax"), ("Synthetic", "Expense Account")]:
			with (
				patch.object(
					frappe.db,
					"get_value",
					return_value=frappe._dict(company=company, account_type=account_type),
				),
				self.assertRaises(frappe.ValidationError),
			):
				setup.AUSimplerBASReportSetup.validate(doc)
		doc.account_1b = doc.account_1a
		with self.assertRaises(frappe.ValidationError):
			setup.AUSimplerBASReportSetup.validate(doc)

	def test_custom_bank_mapping_requires_complete_columns(self):
		rows = [
			frappe._dict(erpnext_column=column, bank_statement_column=column)
			for column in ["Date", "Description", "Deposit", "Withdrawal"]
		]
		doc = frappe._dict(credit_debit_mapping="Single credit&debit", mapping_fields=rows)
		bank_format.validate_mapping(doc)
		rows[0].bank_statement_column = ""
		with self.assertRaises(frappe.ValidationError):
			bank_format.validate_mapping(doc)

	def test_bank_import_checks_later_rows_after_a_matching_first_row(self):
		for name, first in [("Westpac CSV Format", "000000123456"), ("Other", "123456")]:
			with (
				patch.object(frappe.db, "get_value", return_value=("123456", "000-000")),
				self.assertRaisesRegex(frappe.ValidationError, "row 3"),
			):
				bank_statement_import.validate_account_and_branch(
					iter([{"Account": first}, {"Account": "999999"}]),
					frappe._dict(name=name, acc_no_col="Account"),
					"Synthetic",
				)

	def test_boot_settings_never_include_the_abn_credential(self):
		doc = frappe._dict(
			make_tax_category_mandatory=1, abn_lookup_guid="synthetic-only", bas_reporting_period=[]
		)
		with patch.object(frappe, "get_cached_doc", return_value=doc):
			info = {}
			boot.set_bootinfo(info)
		self.assertTrue(info["au_localisation_settings"]["has_abn_lookup_guid"])
		self.assertNotIn("synthetic-only", json.dumps(info))
		self.assertNotIn("abn_lookup_guid", info["au_localisation_settings"])
