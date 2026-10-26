# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.1",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Grid of images with labels: compare keyframes, takes, or the frames on both sides of a cut in one picture.

  uv run contact_sheet.py qc/keyframes.png "keyframes/*.png" --cols 4
  uv run contact_sheet.py qc/junction_S01_S02.png gen/S01/take2.mov@last gen/S02/take1.mov@first --width 800
  uv run contact_sheet.py qc/S03_takes.png gen/S03 --label "take 1" --label "take 2" --title "S03 takes"

Inputs: image files, folders (every image inside, sorted), glob patterns (quote them), and video frames written as
VIDEO@SECONDS, VIDEO@first, VIDEO@mid or VIDEO@last. Labels default to the file name (plus the frame time);
--label (repeatable, in input order) replaces them. Paths are relative to --project (default: current folder).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps

IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi'}
FRAME_AT = re.compile(r'^(?P<path>.+)@(?P<when>first|mid|last|\d+(?:\.\d+)?)$')
BG, FG = (22, 22, 24), (226, 222, 214)


def load_font(size, path=None):
    """A font that can draw CJK labels where the OS has one; Pillow's built-in font otherwise."""
    windir = os.environ.get('WINDIR', 'C:/Windows')
    cands = [path] if path else []
    cands += [f'{windir}/Fonts/msyh.ttc', f'{windir}/Fonts/simhei.ttf', f'{windir}/Fonts/arial.ttf',
              '/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc',
              '/System/Library/Fonts/STHeiti Light.ttc', '/Library/Fonts/Arial Unicode.ttf',
              '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for c in cands:
        if c and Path(c).is_file():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:          # Pillow < 10.1
        return ImageFont.load_default()


def video_frame(path, when):
    """One frame of a video as a PIL image. when: seconds, 'first', 'mid' or 'last'."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'frame.png'
        if when == 'last':
            args = ['-sseof', '-1', '-i', str(path), '-update', '1', str(out)]     # keeps overwriting: last one stays
        else:
            if when == 'mid':
                info = subprocess.run([ff, '-hide_banner', '-i', str(path)], capture_output=True).stderr.decode(
                    'utf-8', 'replace')
                m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', info)
                t = (int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) / 2 if m else 0.0
            else:
                t = 0.0 if when == 'first' else float(when)
            args = ['-ss', f'{t:.3f}', '-i', str(path), '-frames:v', '1', str(out)]
        r = subprocess.run([ff, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', *args], capture_output=True)
        if r.returncode or not out.is_file():
            raise SystemExit(f'{path.name}@{when}: no frame ({r.stderr.decode("utf-8", "replace")[-300:].strip()})')
        return Image.open(out).convert('RGB')


def collect(inputs, project):
    """[(label, loader)] in input order."""
    items = []
    for s in inputs:
        m = FRAME_AT.match(s)
        if m and Path(m['path']).suffix.lower() in VIDEO_EXT:
            p, when = project / m['path'], m['when']
            if not p.is_file():
                raise SystemExit(f'{m["path"]}: not found')
            tag = f'{float(when):.2f}s' if when[0].isdigit() else when
            items.append((f'{p.stem} @{tag}', lambda p=p, w=when: video_frame(p, w)))
            continue
        p = project / s
        if p.is_dir():
            files = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT)
        elif any(ch in s for ch in '*?['):
            files = sorted(f for f in project.glob(s) if f.suffix.lower() in IMAGE_EXT)
        elif p.is_file() and p.suffix.lower() in VIDEO_EXT:
            items.append((f'{p.stem} @first', lambda p=p: video_frame(p, 'first')))
            continue
        elif p.is_file():
            files = [p]
        else:
            raise SystemExit(f'{s}: not found')
        items += [(f.stem, lambda f=f: ImageOps.exif_transpose(Image.open(f)).convert('RGB')) for f in files]
    return items


def build(items, cols, width, labels, title, font, show_labels=True):
    thumbs = []
    for k, (name, load) in enumerate(items):
        im = load()
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
        label = labels[k] if labels and k < len(labels) else name
        thumbs.append((label, im))
    cols = max(1, min(cols, len(thumbs)))
    pad = 12
    lab = (font.size + 14) if show_labels and hasattr(font, 'size') else (24 if show_labels else 0)
    head = (font.size + 24) if title and hasattr(font, 'size') else (34 if title else 0)
    rows = [thumbs[i:i + cols] for i in range(0, len(thumbs), cols)]
    row_h = [max(im.height for _, im in r) for r in rows]
    sheet = Image.new('RGB', (pad + cols * (width + pad), head + sum(h + lab + pad for h in row_h) + pad), BG)
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((pad, 10), title, fill=FG, font=font)
    y = head + pad
    for r, h in zip(rows, row_h):
        for c, (label, im) in enumerate(r):
            x = pad + c * (width + pad)
            if show_labels:
                d.text((x, y + 2), label, fill=FG, font=font)
            sheet.paste(im, (x, y + lab))
        y += h + lab + pad
    return sheet


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('out', help='output image (.png or .jpg), relative to the project')
    ap.add_argument('inputs', nargs='+', help='images, folders, "globs", VIDEO@SECONDS|first|mid|last')
    ap.add_argument('--project', default='.', help='project root (default: .)')
    ap.add_argument('--cols', type=int, default=4, help='columns (default 4)')
    ap.add_argument('--width', type=int, default=480, help='thumbnail width, px (default 480)')
    ap.add_argument('--label', action='append', help='label for the next input image, repeatable')
    ap.add_argument('--no-labels', action='store_true')
    ap.add_argument('--title', help='title line at the top')
    ap.add_argument('--font', help='TTF/TTC font file')
    ap.add_argument('--font-size', type=int, default=20)
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    items = collect(a.inputs, project)
    if not items:
        raise SystemExit('no images found')
    if a.label and len(a.label) != len(items):
        print(f'WARN  {len(a.label)} labels for {len(items)} images; the rest keep their file names')
    sheet = build(items, a.cols, a.width, a.label, a.title, load_font(a.font_size, a.font), not a.no_labels)
    out = project / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in ('.jpg', '.jpeg'):
        sheet.save(out, quality=92)
    else:
        sheet.save(out)
    print(f'SHEET {a.out}  {len(items)} images, {sheet.width}x{sheet.height}')
    return out


if __name__ == '__main__':
    main()
