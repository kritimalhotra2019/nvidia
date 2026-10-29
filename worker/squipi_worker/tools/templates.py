"""Read-only saved-template catalog. Mutations are private native-host IPC."""
from ..registry import tool
from ..templates import library_action


@tool("list_templates", "List the user's saved Word and PowerPoint formatting templates and defaults. Returns ids and format metadata only, never source document text. Use an id with export_document or report_dataset; use 'none' to disable a default.",
      {"type": "object", "properties": {}, "additionalProperties": False})
def list_templates(ctx, args):
    return library_action(ctx.project, {"action": "list"})
