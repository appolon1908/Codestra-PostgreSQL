"""One immutable, independently reviewed historical status-text false positive."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

COMMIT = 'f6ce1dcec2d9ea9a5fc5c6cc9102c3aa9d77b003'
FILE = 'tests/integration.py'
LINE = 392
RULE = 'generic-api-key'
LITERAL_SHA256 = '22682fe6a6d038fdb6c839e725764d3f6080b0832d9c567d666db79e8baab7c5'
FINGERPRINT = f'{COMMIT}:{FILE}:{RULE}:{LINE}'


def verify_status_literal(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == 'print'
                and len(node.value.args) == 1 and not node.value.keywords):
            value = node.value.args[0]
            if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and value.lineno == LINE and node.lineno == LINE - 1
                    and node.end_lineno == LINE + 1):
                return hashlib.sha256(value.value.encode()).hexdigest() == LITERAL_SHA256
    return False


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Expected runner-temporary ignore output path')
    source = subprocess.check_output(['git', 'show', f'{COMMIT}:{FILE}']).decode()
    if not verify_status_literal(source):
        raise SystemExit('Historical status-text proof mismatch; no exception written')
    Path(sys.argv[1]).write_text(FINGERPRINT + '\n')
    print(json.dumps({'classification': 'immutable-print-status-text',
                      'fingerprint': FINGERPRINT, 'literal_sha256': LITERAL_SHA256}))


if __name__ == '__main__':
    main()
