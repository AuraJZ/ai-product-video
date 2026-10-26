# /// script
# requires-python = ">=3.10"
# ///
"""Scan video prompts for risky word families before you pay for a generation (warnings only).

  uv run lint_prompts.py --project P                          # every .txt under P/shots/prompts
  uv run lint_prompts.py --project P --families work/lint_words.json --strict

The built-in families are the ones in references/05-video-gen.md section 4: words that change the task type,
invent a camera move or an exit, make a light source, switch a steady light off, empty quality words and a stated
duration. Product-specific words (mechanisms this product does not have) go in a JSON file passed with --families:
{"not this product": ["喷雾", "蒸汽"], "<another family>": ["word", ...]} (a list of words or one regex string;
a family that already exists is extended). A hit is a prompt to look, not an error: boundary sentences such as
"本身不发光" are often needed, and an intentional light switch-off is written with "关闭" (see 05 section 3).

It prints file:line: family: word and the line, then the number of files scanned. It exits with 1 when no prompt
file was found (a wrong --project must not look like a clean result) and, with --strict, with 2 when anything hit.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FAMILIES = {
    'changes the task type': '编辑|增加|加上|删除|去掉|修改|替换|改成|延长|延续|续写|继续',
    'invents a camera move or an exit': '全景|拉远|俯视|横移|门口|画面左|画面右',
    'makes a light source': '发光|光晕|光波|光束|射线|霓虹',
    'switches a steady light off': '最低|变暗|暗下|熄|呼吸|闪烁',
    'empty quality words': '4K|8K|ARRI|电影级|大师|超高清',
    'states a duration': r'生成一段[^。\n]{0,12}?\d+\s*秒',
}


def load_families(path):
    fam = dict(FAMILIES)
    if not path:
        return fam
    extra = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    for name, words in extra.items():
        if str(name).startswith('_'):
            continue
        pat = words if isinstance(words, str) else '|'.join(re.escape(w) for w in words if w)
        if not pat:
            continue
        fam[name] = f'{fam[name]}|{pat}' if name in fam else pat
    return fam


def scan(files, families, root):
    hits = []
    for f in files:
        text = f.read_text(encoding='utf-8-sig')
        lines = text.splitlines()
        for family, pattern in families.items():
            for m in re.finditer(pattern, text, re.I):
                n = text[:m.start()].count('\n') + 1
                hits.append(dict(file=f.relative_to(root).as_posix(), line=n, family=family, word=m.group(),
                                 text=lines[n - 1].strip()[:80] if n - 1 < len(lines) else ''))
    return sorted(hits, key=lambda h: (h['file'], h['line']))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Scan video prompts for risky word families (see the docstring)')
    ap.add_argument('--project', default='.', type=Path, help='project root (default: current directory)')
    ap.add_argument('--prompts', default='shots/prompts', help='prompt folder, relative to the project')
    ap.add_argument('--families', help='JSON file with extra families / product words, relative to the project')
    ap.add_argument('--strict', action='store_true', help='exit 2 when anything hit')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    root = a.project.resolve()
    folder = root / a.prompts
    files = sorted(folder.rglob('*.txt')) if folder.is_dir() else []
    families = load_families(root / a.families if a.families else None)
    hits = scan(files, families, root)
    for h in hits:
        print(f'{h["file"]}:{h["line"]}: {h["family"]}: {h["word"]}   | {h["text"]}')
    print(f'SCANNED {len(files)} prompt file(s) in {a.prompts}: {len(hits)} hit(s)')
    if not files:
        print(f'No .txt prompt files under {folder}. Wrong --project or --prompts? (nothing was checked)')
        raise SystemExit(1)
    if a.strict and hits:
        raise SystemExit(2)
    return hits


if __name__ == '__main__':
    main()
