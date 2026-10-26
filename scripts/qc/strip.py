# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=1.24",
#     "pillow>=10.1",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Look at a generated clip frame by frame before it goes into the edit.

  uv run strip.py gen/S01/take1.mov                       # -> qc/take1_strip.png, _curves.png, _strip.json
  uv run strip.py gen/S01 gen/S02 --every 0.5 --cols 6    # every video in those folders
  uv run strip.py edit/cut.mp4 --start 40 --end 52        # one stretch of a long edit

Per clip it writes into --out-dir (default <project>/qc):
  <name>_strip.png   a frame every --every seconds (default 0.25), labelled with time, frame number and motion;
                     samples next to a flagged event get a coloured border
  <name>_curves.png  motion and luma for every frame over time, flagged events marked
  <name>_strip.json  per-frame motion / luma / histogram change, the sampled frames and the flags

Flags are heuristics that point the eye; the eye decides:
  cut        motion on one frame far above its neighbours: a hard cut inside what should be one continuous shot
  luma_jump  mean brightness jumps between two frames: flash, exposure pop, or a cut between similar framings
  freeze     motion near zero for a while: a held or frozen frame (or a locked-off shot where nothing moves)
Dissolves (one image fading into another inside a clip), ghosting, doubled parts and shape drift do not spike:
look for them in the strip itself.

Motion = mean absolute luma difference to the previous frame (0-255 scale) at half the thumbnail width.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi'}
logging.getLogger('imageio_ffmpeg').setLevel(logging.ERROR)     # we scale on purpose; skip its size notice
BG, FG, DIM = (22, 22, 24), (226, 222, 214), (140, 140, 146)
COLORS = {'cut': (235, 64, 52), 'luma_jump': (245, 166, 35), 'freeze': (64, 170, 235)}


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


# ---------------------------------------------------------------------------------------------------------------------
# Analysis


def analyse(path, every, thumb_w, start=None, end=None):
    """Decode once. Returns (meta, per-frame rows, {frame index: thumbnail})."""
    inp = ['-ss', f'{start:.3f}'] if start else []
    out = ['-vf', f'scale={thumb_w}:-2']
    if end is not None:
        out = ['-t', f'{end - (start or 0):.3f}'] + out
    gen = imageio_ffmpeg.read_frames(str(path), input_params=inp, output_params=out)
    meta = next(gen)
    w, h = meta['size']
    fps = float(meta.get('fps') or 24.0)
    t0 = float(start or 0)
    rows, thumbs = [], {}
    prev_small = prev_hist = None
    next_t, last = 0.0, None
    for i, raw in enumerate(gen):
        rgb = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        y = rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        small = y[:h // 2 * 2, :w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
        hist = np.histogram(small, bins=32, range=(0, 256))[0] / small.size
        t = i / fps
        rows.append({'i': i, 't': round(t0 + t, 3), 'luma': round(float(small.mean()), 2),
                     'motion': 0.0 if prev_small is None else round(float(np.abs(small - prev_small).mean()), 3),
                     'hist': 0.0 if prev_hist is None else round(float(np.abs(hist - prev_hist).sum()), 4)})
        prev_small, prev_hist = small, hist
        if t >= next_t - 0.5 / fps:
            thumbs[i] = Image.fromarray(rgb.copy())
            next_t += every
        last = (i, rgb)
    if last and last[0] not in thumbs:
        thumbs[last[0]] = Image.fromarray(last[1].copy())     # always show the final frame
    meta = {'fps': fps, 'frames': len(rows), 'duration': round(len(rows) / fps, 3),
            'source_size': list(meta.get('source_size') or meta['size']), 'start': t0}
    return meta, rows, thumbs


def find_flags(rows, fps, cut_min, cut_ratio, cut_hist, luma_jump, freeze_motion, freeze_min, window=12):
    """A cut is one frame whose motion stands far above its neighbours, or whose luma histogram changes far more
    than its neighbours' while motion is high too (catches cuts inside fast camera moves)."""
    m = np.array([r['motion'] for r in rows])
    hs = np.array([r['hist'] for r in rows])
    lum = np.array([r['luma'] for r in rows])
    dl = np.abs(np.diff(lum, prepend=lum[:1]))
    med = lambda x: float(np.median(x)) if len(x) else 0.0      # noqa: E731
    flags = []
    for i in range(1, len(rows)):
        lo, hi = max(1, i - window), min(len(rows), i + window + 1)
        base = med(np.concatenate([m[lo:i], m[i + 1:hi]]))
        hbase = med(np.concatenate([hs[lo:i], hs[i + 1:hi]]))
        dbase = med(np.concatenate([dl[lo:i], dl[i + 1:hi]]))
        ratio = m[i] / max(base, 0.5)
        hratio = hs[i] / max(hbase, 0.02)
        if m[i] >= cut_min and (ratio >= cut_ratio or (hs[i] >= cut_hist and hratio >= cut_ratio)):
            flags.append({'type': 'cut', 't': rows[i]['t'], 'frame': i, 'motion': rows[i]['motion'],
                          'neighbour_median': round(base, 3), 'ratio': round(float(ratio), 1),
                          'hist': rows[i]['hist'], 'hist_ratio': round(float(hratio), 1),
                          'luma_change': round(float(dl[i]), 2)})
        elif dl[i] >= luma_jump and dl[i] >= 3 * max(dbase, 0.5):
            flags.append({'type': 'luma_jump', 't': rows[i]['t'], 'frame': i, 'luma_change': round(float(dl[i]), 2),
                          'motion': rows[i]['motion']})
    run_start = None
    for i in range(1, len(rows) + 1):
        still = i < len(rows) and m[i] < freeze_motion
        if still and run_start is None:
            run_start = i
        elif not still and run_start is not None:
            n = i - run_start                       # frames that repeat the one before them
            if n / fps >= freeze_min:
                flags.append({'type': 'freeze', 't': rows[run_start - 1]['t'], 'end': rows[i - 1]['t'],
                              'frame': run_start - 1, 'seconds': round(n / fps, 3),
                              'max_motion': round(float(m[run_start:i].max()), 3)})
            run_start = None
    return sorted(flags, key=lambda f: f['t'])


# ---------------------------------------------------------------------------------------------------------------------
# Drawing


def draw_strip(thumbs, rows, flags, meta, title, cols, font, small):
    idx = sorted(thumbs)
    tw, th = thumbs[idx[0]].size
    cols = max(1, min(cols, len(idx)))
    pad, lab, head = 8, 24, 40
    nrows = (len(idx) + cols - 1) // cols
    sheet = Image.new('RGB', (pad + cols * (tw + pad), head + nrows * (th + lab + pad) + pad), BG)
    d = ImageDraw.Draw(sheet)
    counts = {k: sum(f['type'] == k for f in flags) for k in COLORS}
    d.text((pad, 10), f'{title}   {meta["duration"]:.2f} s  {meta["fps"]:g} fps  {meta["frames"]} frames  '
           f'{meta["source_size"][0]}x{meta["source_size"][1]}   cuts {counts["cut"]}  luma jumps '
           f'{counts["luma_jump"]}  freezes {counts["freeze"]}', fill=FG, font=font)
    times = [rows[i]['t'] for i in idx]
    for k, i in enumerate(idx):
        x, y = pad + (k % cols) * (tw + pad), head + (k // cols) * (th + lab + pad)
        t, prev = times[k], times[k - 1] if k else -1.0
        hit = None
        for f in flags:
            if f['type'] == 'freeze' and f['t'] <= t <= f['end'] + 1e-6:
                hit = hit or 'freeze'
            elif f['type'] != 'freeze' and prev < f['t'] <= t:
                hit = 'cut' if f['type'] == 'cut' or hit == 'cut' else f['type']
        d.text((x, y + 4), f'{t:.2f}s  f{i}  m{rows[i]["motion"]:.1f}', fill=COLORS.get(hit, FG), font=small)
        sheet.paste(thumbs[i], (x, y + lab))
        if hit:
            d.rectangle([x - 3, y + lab - 3, x + tw + 2, y + lab + th + 2], outline=COLORS[hit], width=3)
    return sheet


def draw_curves(rows, flags, meta, title, samples, font, small, width=1600):
    t = np.array([r['t'] for r in rows])
    m = np.array([r['motion'] for r in rows])
    lum = np.array([r['luma'] for r in rows])
    left, right, top, ph, gap = 70, 20, 44, 200, 46
    H = top + 2 * ph + gap + 50
    im = Image.new('RGB', (width, H), BG)
    d = ImageDraw.Draw(im)
    t0, t1 = (t[0], t[-1]) if len(t) > 1 else (0.0, 1.0)
    t1 = t1 if t1 > t0 else t0 + 1
    X = lambda v: left + (v - t0) / (t1 - t0) * (width - left - right)      # noqa: E731
    d.text((left, 12), f'{title}   motion = mean |luma change| per frame;  luma = mean brightness (0-255)',
           fill=FG, font=font)
    span = t1 - t0
    step = next(s for s in (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300) if span / s <= 20)
    m_top = max(float(np.percentile(m, 99)) * 1.25 if len(m) else 1.0, 2.0, float(m.max()) * 1.05 if len(m) else 1.0)
    l_lo, l_hi = max(0.0, float(lum.min()) - 10), min(255.0, float(lum.max()) + 10)
    if l_hi - l_lo < 20:
        l_lo, l_hi = max(0.0, l_lo - 10), min(255.0, l_hi + 10)
    panels = (('motion', m, 0.0, m_top, top), ('luma', lum, l_lo, l_hi, top + ph + gap))
    for name, v, lo, hi, y0 in panels:
        Y = lambda val, lo=lo, hi=hi, y0=y0: y0 + ph - (val - lo) / (hi - lo) * ph     # noqa: E731
        d.rectangle([left, y0, width - right, y0 + ph], outline=(70, 70, 76))
        for f in flags:
            if f['type'] == 'freeze':
                d.rectangle([X(f['t']), y0 + 1, max(X(f['end']), X(f['t']) + 2), y0 + ph - 1], fill=(28, 52, 70))
        for k in range(int(np.ceil(t0 / step)), int(t1 / step) + 1):
            x = X(k * step)
            d.line([x, y0, x, y0 + ph], fill=(42, 42, 48))
            if name == 'luma':
                d.text((x - 10, y0 + ph + 6), f'{k * step:g}s', fill=DIM, font=small)
        for f in flags:
            if f['type'] != 'freeze':
                x = X(f['t'])
                d.line([x, y0, x, y0 + ph], fill=COLORS[f['type']], width=2)
        for s in samples:
            x = X(s)
            d.line([x, y0 + ph - 5, x, y0 + ph], fill=DIM)
        pts = [(X(a), Y(b)) for a, b in zip(t, v)]
        if len(pts) > 1:
            d.line(pts, fill=FG, width=2)
        d.text((8, y0 + 2), name, fill=FG, font=small)
        d.text((8, y0 + 20), f'{hi:.0f}', fill=DIM, font=small)
        d.text((8, y0 + ph - 18), f'{lo:.0f}', fill=DIM, font=small)
    lx = left
    for k, c in (('cut', COLORS['cut']), ('luma jump', COLORS['luma_jump']), ('freeze', COLORS['freeze'])):
        d.rectangle([lx, H - 22, lx + 14, H - 10], fill=c)
        d.text((lx + 20, H - 26), k, fill=FG, font=small)
        lx += 130
    d.text((lx, H - 26), 'grey ticks: frames in the strip', fill=DIM, font=small)
    return im


# ---------------------------------------------------------------------------------------------------------------------


def expand(inputs, project):
    out = []
    for s in inputs:
        p = project / s
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT)
        elif any(ch in s for ch in '*?['):
            out += sorted(f for f in project.glob(s) if f.suffix.lower() in VIDEO_EXT)
        else:
            out.append(p)
    return out


def rel(p, root):
    try:
        return Path(p).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(p).name


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('clips', nargs='+', help='video files, folders of videos, or glob patterns')
    ap.add_argument('--project', default='.', help='project root; relative paths are resolved here (default: .)')
    ap.add_argument('--out-dir', default='qc', help='output folder inside the project (default: qc)')
    ap.add_argument('--every', type=float, default=0.25, help='seconds between strip frames (default 0.25)')
    ap.add_argument('--cols', type=int, default=8, help='strip columns (default 8)')
    ap.add_argument('--thumb-width', type=int, default=320, help='thumbnail width, px (default 320)')
    ap.add_argument('--start', type=float, help='analyse from this time, s')
    ap.add_argument('--end', type=float, help='analyse up to this time, s')
    ap.add_argument('--cut-min', type=float, default=6.0, help='cut: minimum motion of the spike frame (default 6)')
    ap.add_argument('--cut-ratio', type=float, default=4.0, help='cut: spike / neighbour median (default 4)')
    ap.add_argument('--cut-hist', type=float, default=0.3, help='cut: histogram change (0-2) that also counts '
                    'when it is --cut-ratio times its neighbours (default 0.3)')
    ap.add_argument('--luma-jump', type=float, default=8.0, help='luma jump between frames, 0-255 (default 8)')
    ap.add_argument('--freeze-motion', type=float, default=0.3, help='freeze: motion below this (default 0.3)')
    ap.add_argument('--freeze-min', type=float, default=0.3, help='freeze: at least this long, s (default 0.3)')
    ap.add_argument('--font', help='TTF/TTC font file for labels')
    ap.add_argument('--fail-on-flags', action='store_true', help='exit with code 2 when anything is flagged')
    a = ap.parse_args(argv)
    if a.every <= 0:
        ap.error('--every must be > 0')
    project = Path(a.project).resolve()
    out_dir = project / a.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    font, small = load_font(18, a.font), load_font(14, a.font)
    clips = expand(a.clips, project)
    if not clips:
        raise SystemExit('no video files found')
    stems = [c.stem for c in clips]
    results, any_flag = [], False
    for clip in clips:
        if not clip.is_file():
            print(f'MISSING {clip.name}')
            continue
        name = clip.stem if stems.count(clip.stem) == 1 else f'{clip.parent.name}_{clip.stem}'
        if a.start or a.end is not None:
            name += f'_{a.start or 0:g}-' + (f'{a.end:g}' if a.end is not None else 'end')
        meta, rows, thumbs = analyse(clip, a.every, a.thumb_width, a.start, a.end)
        if not rows:
            print(f'EMPTY {rel(clip, project)}: no frames decoded')
            continue
        flags = find_flags(rows, meta['fps'], a.cut_min, a.cut_ratio, a.cut_hist, a.luma_jump, a.freeze_motion,
                           a.freeze_min)
        title = rel(clip, project)
        samples = [rows[i]['t'] for i in sorted(thumbs)]
        draw_strip(thumbs, rows, flags, meta, title, a.cols, font, small).save(out_dir / f'{name}_strip.png')
        draw_curves(rows, flags, meta, title, samples, font, small).save(out_dir / f'{name}_curves.png')
        m = np.array([r['motion'] for r in rows[1:]] or [0.0])
        summary = {'clip': title, **meta, 'every': a.every,
                   'thresholds': {k: getattr(a, k) for k in ('cut_min', 'cut_ratio', 'cut_hist', 'luma_jump',
                                                             'freeze_motion', 'freeze_min')},
                   'motion_median': round(float(np.median(m)), 3), 'motion_p95': round(float(np.percentile(m, 95)), 3),
                   'luma_range': [min(r['luma'] for r in rows), max(r['luma'] for r in rows)],
                   'flags': flags, 'samples': [rows[i] for i in sorted(thumbs)], 'per_frame': rows}
        (out_dir / f'{name}_strip.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                                    encoding='utf-8')
        any_flag |= bool(flags)
        print(f'{title}: {meta["duration"]:.2f} s, {meta["frames"]} frames, motion median {summary["motion_median"]}'
              f' -> {rel(out_dir / (name + "_strip.png"), project)}')
        for f in flags:
            extra = (f'until {f["end"]:.2f} s ({f["seconds"]:.2f} s)' if f['type'] == 'freeze' else
                     f'motion {f["motion"]} vs {f.get("neighbour_median", "-")} around' if f['type'] == 'cut' else
                     f'luma change {f["luma_change"]}')
            print(f'  {f["type"].upper():<10} {f["t"]:7.2f} s  frame {f["frame"]:<5} {extra}')
        results.append(summary)
    if a.fail_on_flags and any_flag:
        raise SystemExit(2)
    return results


if __name__ == '__main__':
    main()
