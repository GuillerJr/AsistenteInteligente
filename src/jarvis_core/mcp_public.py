"""Model/durable views exclude app-only metadata and executable app documents."""
from collections.abc import Mapping


def app_mime(value):
    if not isinstance(value, str):
        return False
    parts = value.split(';')
    parameters = {}
    for item in parts[1:]:
        key, separator, parameter = item.partition('=')
        if separator:
            parameters[key.strip().lower()] = parameter.strip().strip('"\'').lower()
    return parts[0].strip().lower() == "text/html" and parameters.get("profile") == "mcp-app"


def public_content(value):
    if isinstance(value, Mapping):
        if app_mime(value.get("mimeType")):
            return {"uri": value.get("uri"), "mimeType": value["mimeType"],
                    "app_view_required": True, "content_redacted": True}
        return {key: public_content(item) for key, item in value.items() if key != "_meta"}
    if isinstance(value, (tuple, list)):
        return [public_content(item) for item in value]
    return value
