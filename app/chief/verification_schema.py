"""Provider-enforced JSON structure; semantic validation still runs locally."""
PLAN_SCHEMA = {
    'type': 'object', 'required': ['version', 'requirements', 'checks'],
    'properties': {
        'version': {'type': 'integer', 'enum': [1]},
        'requirements': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['id', 'description', 'required_categories'],
            'properties': {'id': {'type': 'string', 'minLength': 1}, 'description': {'type': 'string'},
                'required_categories': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                    'items': {'type': 'string', 'enum': ['normal', 'invalid', 'boundary']}},
                'category_rationale': {'type': 'string'}}}},
        'checks': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['id', 'kind', 'target', 'requirements'],
            'properties': {'id': {'type': 'string', 'minLength': 1}, 'kind': {'type': 'string',
                'enum': ['python_function', 'json_value', 'text', 'file', 'command', 'gui', 'image_output', 'visual_review']},
                'target': {'type': 'string'}, 'requirements': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
                'category': {'type': 'string', 'enum': ['normal', 'invalid', 'boundary']},
                'required': {'type': 'boolean'}}}}
    }
}

# A function check must include its callable and test cases, rather than only a label.
PLAN_SCHEMA['properties']['checks']['items']['anyOf'] = [
    {'properties': {'kind': {'const': 'python_function'},
        'function': {'type': 'string', 'pattern': '^[A-Za-z_][A-Za-z0-9_]*$'},
        'cases': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['args', 'category'],
            'properties': {'args': {'type': 'array'},
                'category': {'enum': ['normal', 'invalid', 'boundary']},
                'expected': {}, 'error': {'type': 'boolean'}},
            'anyOf': [{'required': ['expected']}, {'required': ['error'], 'properties': {'error': {'const': True}}}]}}},
     'required': ['function', 'cases']},
    {'properties': {'kind': {'enum': ['json_value', 'text', 'file', 'command', 'gui', 'image_output', 'visual_review']}}}
]


def normalize_categories(contract):
    """Only remove exact duplicates; never translate unknown values or expectations."""
    from copy import deepcopy
    result = deepcopy(contract)
    for requirement in result.get('requirements', []):
        categories = requirement.get('required_categories')
        if isinstance(categories, list) and all(isinstance(x, str) for x in categories):
            requirement['required_categories'] = list(dict.fromkeys(categories))
    return result
