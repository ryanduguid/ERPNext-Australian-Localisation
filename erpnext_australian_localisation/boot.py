import frappe


def set_bootinfo(bootinfo):
	settings = frappe.get_cached_doc("AU Localisation Settings")
	bootinfo["au_localisation_settings"] = {
		"make_tax_category_mandatory": settings.make_tax_category_mandatory,
		"has_abn_lookup_guid": bool(settings.abn_lookup_guid),
		"bas_reporting_period": [
			{field: row.get(field) for field in ("company", "reporting_period", "reporting_method")}
			for row in settings.bas_reporting_period
		],
	}
