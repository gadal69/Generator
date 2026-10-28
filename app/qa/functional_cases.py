"""Literal function-call shorthand. No domain-specific examples or expected answers."""
import ast
import re
import json


def cases_from_request(request):
    cases = []
    # General literal calls, e.g. add(2, 3) = 5 or calculate("2+3*4") -> 14.
    pattern = r'(?<![\w.])([A-Za-z]\w*)\(([^\n]{0,256}?)\)\s*(?:==|=|->|→)\s*(-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?|True|False|None|"[^"\n]*"|\x27[^\x27\n]*\x27)(?![\w]|\.\d|\s*[+*/])'
    for match in re.finditer(pattern, request):
        try:
            call = ast.parse('f(' + match[2] + ')', mode='eval').body
            if call.keywords:
                continue
            args = [ast.literal_eval(arg) for arg in call.args]
            expected = ast.literal_eval(match[3])
            if json.loads(json.dumps(args)) != args:
                continue
            cases.append({'function': match[1], 'args': args, 'expected': expected})
        except (SyntaxError, ValueError, TypeError):
            continue
    unique = []
    for case in cases:
        if case not in unique:
            unique.append(case)
    return unique[:32]



verification_cases_from_request = cases_from_request
