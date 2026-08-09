import html

def sanitize_dict(data, exclude_keys=None):
    """
    Recursively walks through a dictionary or list, HTML-escaping all string values.
    Accepts exclude_keys set to skip sensitive credential/identifier fields.
    """
    if exclude_keys is None:
        exclude_keys = {'password', 'confirm_password', 'current_password', 'new_password', 'token', 'device_id'}

    if isinstance(data, str):
        # HTML-escape strings to prevent XSS
        return html.escape(data.strip())
    elif isinstance(data, list):
        return [sanitize_dict(item, exclude_keys) for item in data]
    elif isinstance(data, dict):
        return {
            k: (v if k in exclude_keys else sanitize_dict(v, exclude_keys))
            for k, v in data.items()
        }
    return data
