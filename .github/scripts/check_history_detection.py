"""Prove the one historical fingerprint cannot suppress a credential-like neighbor."""
import json
from pathlib import Path
import secrets
import string
import subprocess
import sys
import tempfile


def main():
    binary, ignore = sys.argv[1:]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        def git(*args):
            return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
        git('init')
        git('config', 'user.name', 'Local scanner regression')
        git('config', 'user.email', 'fixture@example.invalid')
        # Generated locally, never issued by any provider and never printed.
        fixture = 'ghp' + '_' + ''.join(secrets.choice(string.ascii_letters + string.digits)
                                       for _ in range(36))
        (root / 'fixture.py').write_text('print("public status summary")\ncredential = ' + repr(fixture) + '\n')
        git('add', 'fixture.py')
        git('commit', '-m', 'local synthetic scanner regression')
        report = root / 'redacted.json'
        result = subprocess.run([binary, 'git', '--no-banner', '--redact=100', '--exit-code', '1',
                                 '--gitleaks-ignore-path', ignore, '--report-format', 'json',
                                 '--report-path', str(report), '.'], cwd=root, capture_output=True)
        if result.returncode != 1 or not report.exists():
            raise SystemExit('Scanner did not reject credential-like adjacent-line fixture')
        findings = json.loads(report.read_text())
        if not any(f['RuleID'] == 'github-pat' and f['StartLine'] == 2 for f in findings):
            raise SystemExit('Adjacent-line credential detector regression failed')
        print('Credential-like adjacent-line fixture correctly rejected; historical exception remains exact')


if __name__ == '__main__':
    main()
