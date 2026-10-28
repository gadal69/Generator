"""Provider-specific settings selected by configuration, never by model family."""
from copy import deepcopy


def generation_settings(config, provider, model, base_options, timeout, stage, attempt=0):
    options = deepcopy(base_options)
    request_options = {}
    profile = config.get('model_profiles', {}).get(model, {}).get(provider, {})
    for settings in (profile, config.get('generation', {}).get(stage, {}),
                     profile.get('stages', {}).get(stage, {})):
        options.update(settings.get('options', {}))
        request_options.update(settings.get('request_options', {}))
        retries = settings.get('retry_options', [])
        if attempt > 0 and retries:
            options.update(retries[min(attempt - 1, len(retries) - 1)])
        timeout = settings.get('timeout', timeout)
    # Only explicit provider options are sent; unsupported thinking flags are never guessed.
    return options, float(timeout), request_options


def prepare_adapter(adapter, stage, attempt=0):
    configure = getattr(adapter, 'configure_generation', None)
    if callable(configure):
        configure(stage, attempt)
    return adapter


def mark_incomplete(text, adapter):
    metadata = getattr(adapter, 'last_response', None)
    if isinstance(metadata, dict) and metadata.get('truncated'):
        return '[MODEL_RESPONSE_INCOMPLETE]\n' + text
    return text


def record_response(database, adapter, stage, project_id=None, task_id=None):
    import json
    import uuid
    from datetime import datetime
    metadata = getattr(adapter, 'last_response', None)
    if not isinstance(metadata, dict) or not metadata:
        return
    with database.connection() as conn:
        conn.execute('INSERT INTO model_responses VALUES (?,?,?,?,?,?,?)',
                     ('CALL-' + uuid.uuid4().hex, project_id, task_id, stage,
                      str(getattr(adapter, 'model', '')), json.dumps(metadata, ensure_ascii=False),
                      datetime.now().isoformat()))
