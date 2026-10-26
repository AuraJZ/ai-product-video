# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.1",
# ]
# ///
"""Normalise painted keyframes before they go to a video model: keyframes/raw -> keyframes/norm.

  uv run normalise_keyframes.py --project P --size 1080x1920        # vertical film
  uv run normalise_keyframes.py --project P --size 1920x1080 --dry  # show what would happen

Shot frames (role first / mid / last) are centre-cropped to the exact output aspect and resized to --size, so first
and last frames of a shot match exactly. Masters and references (role master / ref) are never cropped: a landscape
product reference sheet stays whole and is only shrunk to fit the output frame in its own orientation.
Roles come from keyframes/keyframes.json ({"id", "role", "save_as"} entries, a list or {"keyframes": [...]});
without it, ids that start with M or R plus a digit (M1, R2) count as masters / references.
Reads .png .jpg .jpeg .webp (image models return all of them); writes <id>.png. EXIF rotation is applied and
transparency is flattened onto white.

Warnings (also in the summary): UPSCALED (the source is smaller than the output: the frame gets soft; paint or
download it larger), CROPPED (the source's aspect is more than 5 % off the output: wrong orientation or a wrong
canvas, check before using it), DUPLICATE (the same id in two formats: neither is used). --fail-on-warn exits with 2.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps

EXT = ('.png', '.jpg', '.jpeg', '.webp')
FIT_ROLES = ('master', 'ref')
MASTER_ID = re.compile(r'^[MR]\d')


def load_roles(path):
    """id -> role from keyframes.json (missing file: {})."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    items = data.get('keyframes', []) if isinstance(data, dict) else data
    roles = {}
    for k in items:
        if isinstance(k, dict) and k.get('id'):
            roles[str(k['id'])] = str(k.get('role', ''))
            if k.get('save_as'):
                roles.setdefault(Path(k['save_as']).stem, str(k.get('role', '')))
    return roles


def flatten(im):
    im = ImageOps.exif_transpose(im)
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel('A'))
        return bg
    return im.convert('RGB')


def normalise(im, W, H, fit):
    """Returns (image, notes, warnings)."""
    w, h = im.size
    notes, warns = [], []
    if fit:
        box = (max(W, H), min(W, H)) if w >= h else (min(W, H), max(W, H))
        s = min(1.0, box[0] / w, box[1] / h)
        if s < 1:
            im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
            notes.append(f'shrunk to {im.width}x{im.height}')
        else:
            notes.append('kept (fits)')
        return im, notes, warns
    r, R = w / h, W / H
    off = max(r, R) / min(r, R) - 1
    if r > R:
        nw = round(h * R)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    elif r < R:
        nh = round(w / R)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    if im.size != (w, h):
        notes.append(f'cropped to {im.width}x{im.height}')
    if off > 0.05:
        warns.append(f'CROPPED: {w}x{h} is {off:.0%} off {W}x{H}; {1 - min(r, R) / max(r, R):.0%} of the picture '
                     'was cut (wrong orientation or canvas?)')
    if im.width < W * 0.98:
        warns.append(f'UPSCALED: {im.width}x{im.height} -> {W}x{H} (x{W / im.width:.2f}); a larger source keeps '
                     'more detail')
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    notes.append(f'-> {W}x{H}')
    return im, notes, warns


def main(argv=None):
    ap = argparse.ArgumentParser(description='Normalise keyframes: raw -> norm (see the module docstring)')
    ap.add_argument('--project', default='.', type=Path, help='project root (default: current directory)')
    ap.add_argument('--size', required=True, help='output frame WxH, e.g. 1080x1920 (vertical) or 1920x1080')
    ap.add_argument('--src', default='keyframes/raw', help='input folder, relative to the project')
    ap.add_argument('--dst', default='keyframes/norm', help='output folder, relative to the project')
    ap.add_argument('--keyframes', default='keyframes/keyframes.json', help='keyframe list with roles (optional)')
    ap.add_argument('--only', action='append', help='only these ids (repeatable)')
    ap.add_argument('--dry', action='store_true', help='report only, write nothing')
    ap.add_argument('--fail-on-warn', action='store_true', help='exit 2 when anything was upscaled, cropped hard '
                                                                 'or duplicated')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    m = re.fullmatch(r'\s*(\d+)\s*[xX*]\s*(\d+)\s*', a.size)
    if not m:
        raise SystemExit(f'--size must look like 1080x1920, got {a.size!r}')
    W, H = int(m.group(1)), int(m.group(2))
    src, dst = a.project / a.src, a.project / a.dst
    if not src.is_dir():
        raise SystemExit(f'{a.src}: folder not found under the project (wrong --project?)')
    roles = load_roles(a.project / a.keyframes)
    files = {}
    for f in sorted(src.iterdir()):
        if f.suffix.lower() in EXT and f.is_file():
            files.setdefault(f.stem, []).append(f)
    if a.only:
        files = {k: v for k, v in files.items() if k in set(a.only)}
    if not files:
        raise SystemExit(f'no .png / .jpg / .jpeg / .webp files in {a.src}')
    if not a.dry:
        dst.mkdir(parents=True, exist_ok=True)
    report = {'size': [W, H], 'done': [], 'warnings': []}
    for stem, fs in files.items():
        if len(fs) > 1:
            msg = f'{stem}: DUPLICATE in {len(fs)} formats ({", ".join(f.name for f in fs)}); keep one, skipped'
            print('WARN', msg)
            report['warnings'].append(msg)
            continue
        f = fs[0]
        role = roles.get(stem) or ('master' if MASTER_ID.match(stem) else 'frame')
        fit = role in FIT_ROLES
        with Image.open(f) as im0:
            im = flatten(im0)
        size0 = im.size
        im, notes, warns = normalise(im, W, H, fit)
        line = f'{f.name:<22} {role:<7} {size0[0]}x{size0[1]}  ' + '; '.join(notes)
        print(line)
        for w in warns:
            print('WARN', f'{stem}: {w}')
            report['warnings'].append(f'{stem}: {w}')
        if not a.dry:
            im.save(dst / f'{stem}.png')
        report['done'].append({'id': stem, 'role': role, 'from': list(size0), 'to': list(im.size)})
    print(f'\n{len(report["done"])} keyframe(s) {"checked (--dry)" if a.dry else "written to " + a.dst}, '
          f'{len(report["warnings"])} warning(s)')
    if a.fail_on_warn and report['warnings']:
        raise SystemExit(2)
    return report


if __name__ == '__main__':
    main()
