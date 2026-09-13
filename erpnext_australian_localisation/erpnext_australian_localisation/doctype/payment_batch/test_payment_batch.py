# Copyright (c) 2025, frappe.dev@arus.co.in and Contributors
# See license.txt

import re
import sqlite3
from inspect import unwrap
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext_australian_localisation.erpnext_australian_localisation.doctype.payment_batch import (
	aba_file_generator,
	payment_batch,
)


class TestPaymentBatch(TestCase):
	def setUp(self):
		self.bank = frappe._dict(
			currency="AUD",
			company="Synthetic Company",
			apca_number="000000",
			bank_account_no="111111111",
			branch_code="000-000",
			fi_abbr="ANZ",
		)
		self.party = frappe._dict(
			bank_account_no="222222222",
			branch_code="000-000",
			supplier_name="Synthetic Supplier",
		)
		self.row = frappe._dict(
			payment_entry="Synthetic Payment",
			party_type="Supplier",
			party="Synthetic Supplier",
			amount=12.34,
		)
		self.batch = frappe._dict(
			currency="AUD",
			bank_account="Synthetic Bank",
			posting_date="2026-09-10",
			total_paid_amount=12.34,
			payment_created=[self.row],
			name="Synthetic Batch",
			file_format="ABA",
		)
		self.values = patch.object(frappe.db, "get_value", side_effect=self.get_value)
		self.values.start()
		self.addCleanup(self.values.stop)

	def get_value(self, doctype, *args, **kwargs):
		if doctype == "Bank Account":
			return self.bank
		return "SYNTHETIC" if doctype == "Payment Entry" else self.party

	def test_aba_preserves_account_amount_and_record_width(self):
		for amount, expected in [(12.34, "0000001234"), (99999999.99, "9999999999")]:
			with self.subTest(amount=amount):
				self.row.amount = self.batch.total_paid_amount = amount
				lines = aba_file_generator.generate_aba_file(self.batch).splitlines()
				self.assertEqual([len(line) for line in lines], [120, 120, 120])
				self.assertEqual(lines[1][8:17], "222222222")
				self.assertEqual(lines[1][87:96], "111111111")
				self.assertEqual(lines[1][20:30], expected)
				self.assertEqual(lines[2][20:40], expected * 2)

	def test_aba_refuses_long_accounts_on_both_sides(self):
		for account in (self.bank, self.party):
			with self.subTest(account=account):
				original = account.bank_account_no
				account.bank_account_no = "1234567890"
				with self.assertRaises(frappe.ValidationError):
					aba_file_generator.generate_aba_file(self.batch)
				account.bank_account_no = original

	def test_aba_refuses_invalid_or_overflowing_amounts(self):
		for amount in (0, -1, 0.001, 12.345, 100000000, float("inf"), float("nan"), "invalid"):
			with self.subTest(amount=amount):
				self.row.amount = self.batch.total_paid_amount = amount
				with self.assertRaises(frappe.ValidationError):
					aba_file_generator.generate_aba_file(self.batch)

	def test_aba_refuses_overflowing_batch_total(self):
		self.row.amount = 50000000
		self.batch.payment_created = [self.row, self.row]
		self.batch.total_paid_amount = 100000000
		with self.assertRaises(frappe.ValidationError):
			aba_file_generator.generate_aba_file(self.batch)

	def test_aba_refuses_total_that_differs_from_encoded_payments(self):
		self.batch.total_paid_amount = 12.35
		with self.assertRaises(frappe.ValidationError):
			aba_file_generator.generate_aba_file(self.batch)

	def test_invalid_replacement_keeps_previous_attachment(self):
		with (
			patch.object(payment_batch, "generate_aba_file", side_effect=frappe.ValidationError),
			patch.object(frappe.db, "exists", return_value="Previous File"),
			patch.object(frappe, "delete_doc") as delete,
		):
			with self.assertRaises(frappe.ValidationError):
				payment_batch.PaymentBatch.generate_bank_file(self.batch)
			delete.assert_not_called()

	def test_payment_lookup_checks_bank_and_company_access(self):
		for forbidden in ("Bank Account", "Company"):
			with self.subTest(forbidden=forbidden):
				bank = Mock(company="Synthetic Company", account="Synthetic Account")
				company = Mock()
				(
					bank if forbidden == "Bank Account" else company
				).check_permission.side_effect = frappe.PermissionError
				with (
					patch.object(frappe, "get_doc", side_effect=[bank, company]),
					patch.object(frappe.db, "sql") as query,
				):
					with self.assertRaises(frappe.PermissionError):
						self.lookup()
					query.assert_not_called()

	def test_payment_lookup_refuses_cross_company_account(self):
		bank = Mock(company="Different Company")
		with patch.object(frappe, "get_doc", return_value=bank), patch.object(frappe.db, "sql") as query:
			with self.assertRaises(frappe.PermissionError):
				self.lookup()
			query.assert_not_called()

	def test_payment_lookup_requires_payment_entry_read_access(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError),
			patch.object(frappe, "get_doc", return_value=Mock(company="Synthetic Company")),
			patch.object(frappe.db, "sql") as query,
		):
			with self.assertRaises(frappe.PermissionError):
				self.lookup()
			query.assert_not_called()

	def test_payment_lookup_applies_framework_permission_conditions(self):
		bank = Mock(company="Synthetic Company", account="Synthetic Account")
		condition = " and (`tabPayment Entry`.`owner` = 'synthetic-user')"
		with (
			patch.object(frappe, "get_doc", return_value=bank),
			patch.object(payment_batch, "get_match_cond", return_value=condition, create=True),
			patch.object(frappe.db, "sql", return_value=[]) as query,
		):
			self.assertEqual(self.lookup(), [])
			self.assertIn(condition, query.call_args.args[0])
			self.assertEqual(query.call_args.args[1]["paid_from"], "Synthetic Account")

	def test_compound_permissions_cannot_override_payment_filters(self):
		connection = sqlite3.connect(":memory:")
		self.addCleanup(connection.close)
		connection.execute(
			"CREATE TABLE `tabPayment Entry` (name, party_name, base_paid_amount, docstatus, party_type, company, paid_from, owner)"
		)
		connection.execute("CREATE TABLE `tabPayment Batch Item` (payment_entry, party_name, amount, parent)")
		connection.execute("CREATE TABLE `tabPayment Batch` (name, docstatus)")
		valid = [
			"allowed",
			"Supplier",
			12,
			0,
			"Supplier",
			"Synthetic Company",
			"Synthetic Account",
			"allowed",
		]
		rows = [valid]
		for column, value in ((3, 1), (4, "Customer"), (5, "Other Company"), (6, "Other Account")):
			row = valid.copy()
			row[0], row[7], row[column] = f"excluded-{column}", "shared", value
			rows.append(row)
		connection.executemany("INSERT INTO `tabPayment Entry` VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

		def execute(query, parameters, **kwargs):
			query = re.sub(r"%\((\w+)\)s", r":\1", query)
			return connection.execute(query, parameters).fetchall()

		for condition in (
			"",
			" and owner = 'allowed' or owner = 'shared'",
			" and (owner = 'allowed' or owner = 'shared')",
		):
			with (
				self.subTest(condition=condition),
				patch.object(
					frappe,
					"get_doc",
					return_value=Mock(company="Synthetic Company", account="Synthetic Account"),
				),
				patch.object(payment_batch, "get_match_cond", return_value=condition),
				patch.object(frappe.db, "sql", side_effect=execute),
			):
				self.assertEqual(self.lookup(), [("allowed", "Supplier", 12)])

	def lookup(self):
		return unwrap(payment_batch.get_payment_entry)(
			"Payment Entry",
			"",
			"name",
			0,
			20,
			{"bank_account": "Synthetic Bank", "company": "Synthetic Company", "party_type": "Supplier"},
		)

	def test_payment_lookup_excludes_active_batches_by_id_and_keeps_cancelled_batches(self):
		connection = sqlite3.connect(":memory:")
		self.addCleanup(connection.close)
		connection.execute(
			"CREATE TABLE `tabPayment Entry` (name, party_name, base_paid_amount, docstatus, party_type, company, paid_from)"
		)
		connection.execute("CREATE TABLE `tabPayment Batch Item` (payment_entry, party_name, amount, parent)")
		connection.execute("CREATE TABLE `tabPayment Batch` (name, docstatus)")
		for name in ("active", "cancelled", "free"):
			connection.execute(
				"INSERT INTO `tabPayment Entry` VALUES (?, 'Renamed Supplier', 20, 0, 'Supplier', 'Synthetic Company', 'Synthetic Account')",
				(name,),
			)
		connection.executemany("INSERT INTO `tabPayment Batch` VALUES (?, ?)", [("b1", 1), ("b2", 2)])
		connection.executemany(
			"INSERT INTO `tabPayment Batch Item` VALUES (?, 'Old Supplier', 10, ?)",
			[("active", "b1"), ("cancelled", "b2")],
		)

		def execute(query, parameters, **kwargs):
			return connection.execute(re.sub(r"%\((\w+)\)s", r":\1", query), parameters).fetchall()

		with (
			patch.object(
				frappe, "get_doc", return_value=Mock(company="Synthetic Company", account="Synthetic Account")
			),
			patch.object(payment_batch, "get_match_cond", return_value=""),
			patch.object(frappe.db, "sql", side_effect=execute),
		):
			self.assertEqual([row[0] for row in self.lookup()], ["cancelled", "free"])

	def test_generated_attachment_is_scoped_to_its_batch(self):
		batch = Mock(file_format="ABA", bank_file_url="old")
		batch.name = "Synthetic Batch"
		file = Mock(file_url="new")
		with (
			patch.object(payment_batch, "generate_aba_file", return_value="synthetic-content"),
			patch.object(frappe.db, "exists", return_value="Previous File") as exists,
			patch.object(frappe, "delete_doc"),
			patch.object(frappe, "get_doc", return_value=file) as create,
			patch.object(frappe.db, "commit") as commit,
		):
			self.assertEqual(payment_batch.PaymentBatch.generate_bank_file(batch), "new")
			self.assertEqual(exists.call_args.args[1]["attached_to_name"], batch.name)
			self.assertEqual(create.call_args.args[0]["attached_to_doctype"], "Payment Batch")
			self.assertEqual(create.call_args.args[0]["attached_to_name"], batch.name)
			self.assertEqual(create.call_args.args[0]["is_private"], 1)
			commit.assert_not_called()

	def test_missing_email_lookup_checks_parent_permission(self):
		doc = Mock()
		doc.check_permission.side_effect = frappe.PermissionError
		with patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe, "get_all") as rows:
			with self.assertRaises(frappe.PermissionError):
				payment_batch.get_missing_email_suppliers("Synthetic Batch")
			rows.assert_not_called()
