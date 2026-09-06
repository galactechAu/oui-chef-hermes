#!/usr/bin/env python3
"""Check actual inline and external JavaScript syntax with Node, without executing it."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import sys


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.module = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        attrs = dict(attrs)
        self.module = attrs.get('type') == 'module'
        self.active = 'src' not in attrs and attrs.get('type', '') in ('', 'text/javascript', 'application/javascript', 'module')
        self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.scripts.append((''.join(self.parts), self.module))
            self.active = False


def check_tree(root):
    node = shutil.which('node')
    if not node:
        return ['Node.js is required for JavaScript syntax validation']
    sources = []
    for path in sorted(Path(root).rglob('*')):
        if path.suffix == '.html':
            parser = Scripts()
            parser.feed(path.read_text())
            sources.extend((f'{path.name}:script-{i+1}', text, module) for i, (text, module) in enumerate(parser.scripts))
        elif path.suffix in ('.js', '.mjs'):
            sources.append((path.name, path.read_text(), path.suffix == '.mjs'))
    errors = []
    for label, source, module in sources:
        args = [node, '--check']
        if module:
            args.append('--input-type=module')
        result = subprocess.run(args, input=source, text=True, capture_output=True, timeout=30)
        if result.returncode:
            errors.append(f'{label}: {result.stderr.strip()}')
    return errors


if __name__ == '__main__':
    errors = check_tree(Path(__file__).resolve().parents[1] / 'static')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        sys.exit(1)
    print('JavaScript syntax checks passed')
