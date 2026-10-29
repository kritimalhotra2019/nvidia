"""Bounded local bridge; existing worker schemas and file grants remain authoritative."""

from squipi_worker.registry import registry
from squipi_worker.tools.tabular_export import EXCEL_SCHEMA, export_excel

ALLOWED_TOOLS = ('import_dataset', 'profile_dataset', 'set_column_roles', 'clean_dataset', 'aggregate',
                 'correlate', 'compare_groups', 'create_chart', 'find_insights',
                 'read_document', 'report_dataset', 'export_document', 'export_excel',
                 'begin_template_presentation', 'inspect_presentation_draft',
                 'edit_presentation_slide', 'finalize_template_presentation')

def definitions():
    result = []
    for name in ALLOWED_TOOLS:
        item = registry.get(name)
        result.append({'type': 'function', 'function': {
            'name': name,
            'description': item.description if item else 'Export the selected dataset to a real Excel workbook. Use the cleaned dataset ID after PII removal.',
            'parameters': item.params if item else EXCEL_SCHEMA}})
    return result
