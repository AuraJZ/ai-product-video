# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.1",
#     "numpy>=1.24",
#     "imageio-ffmpeg>=0.5.1",
# ]
# ///
"""EDL-driven editor: cuts generated / filmed clips, title cards, transitions, captions, leader-line callouts and a
corner label into one H.264 file, from JSON files you can diff, review and re-render at any size.

uv run edit.py [--project DIR] [--edl edl.json] [--captions FILE] [--callouts FILE]
               [--res 480|720|1080 | --size WxH]  (default: the EDL "size", else 854x480) [--out FILE] [--dry] [--fresh] [--jobs N] [--crf N]
               [--offset-basis trimmed|source] [--bridges FILE] [--drafts FILE]

All relative paths (in the arguments and inside the JSON files) are resolved against --project (default: the
current directory). ffmpeg comes from the imageio-ffmpeg package. Frame size: --size WxH (any frame, both sides
even); else --res N = short side N in the EDL's "size" / "aspect" (default 16:9: 480 = 854x480; a 9:16 EDL gives
480x854); else the EDL "size"; else 854x480. When most sources have the other orientation than the output, a
warning says so (a vertical project rendered landscape by mistake).

EDL (edl.json)
  {"fps": 24, "size": [1080, 1920], "label": "AI 生成画面 · 仅作功能示意",
   "captions_file": "captions.json", "callouts_file": "callouts.json",
   "approved_drafts_file": "approved_drafts.json", "approved_bridges_file": "approved_bridges.json",
   "ledger_pattern": "gen/{id}/{task}",
   "lint": {"banned_extra": ["..."], "max_cjk": 12},
   "items": [
     {"type": "clip", "id": "S02", "src": "footage/S02.mp4", "in": 0.0, "out": 5.0, "speed": 1.0},
     {"type": "transition", "kind": "cut|dissolve|dip_black|dip_white|flare|whip", "dur": 0.5},
     {"type": "bridge", "id": "B_S02_S04", "in": 0.0, "out": 2.5, "fallback": {"kind": "dissolve", "dur": 0.6}},
     {"type": "card", "id": "title", "dur": 2.5, "zh": "...", "en": "...", "style": "title|chapter|end"}],
   "captions": [{"anchor": "S02", "offset": 1.0, "dur": 3.0, "zh": "...", "en": "...", "pos": "lower_left"}]}
  "size" = the delivery frame (or "aspect": "9:16"); --size / --res override it. Keys starting with "_" are
  comments. Any item, caption or callout may carry "portrait" / "landscape" / "square"
  sub-dicts that are merged over it for that output orientation (e.g. "portrait": {"fit": "blur"}), and
  "skip": true to leave it out.

  clip    src = a media file (relative to the project). Without src the clip is looked up in the approved-drafts
          ledger (EDL "approved_drafts_file" or --drafts): {clip_id: value}, value = a media path, a task id
          (resolved through "ledger_pattern", default "gen/{id}/{task}", trying .mov .mp4 .m4v .mkv .webm when the
          pattern has no extension), or {"task": ..., "src": ...}. A clip with no source (or a missing file)
          becomes a labelled black slate of the same length, so the timing can be reviewed before every shot exists.
          "out" defaults to the source end ("out": "end" says so explicitly); optional "speed" (<1 = slow motion),
          "interp" (blend|mci|dup; default blend when slowed), "hold_start" / "hold_end" (s, freeze frame).
          "hold_end": 4.5 freezes the frame at "out" for 4.5 s after the clip has played (out "end" or omitted =
          the source's very last frame; out 4.2 = hold the frame at 4.2 s instead).
          Framing when the source aspect differs from the output (e.g. 16:9 footage in a 9:16 cut):
          "fit": "cover" (default: fill the frame and crop), "contain" (whole picture, black bars) or "blur" (whole
          picture over a blurred, darkened fill of itself); "focus": [x, y] (0-1, default [0.5, 0.5]) moves the
          cover crop window, e.g. [0.3, 0.5] keeps the left-centre of a wide shot in a vertical frame.
          Callouts (leader-line labels, see below): "callouts": "<group id>" | {"group": id, "at"?, "end"?,
          "stagger"?, "fade_out"?, "items"?: [ids]} and/or "annotations": [{"ref": "<group>:<item id>" or
          "<item id>", "start": t, ...item overrides}].
  bridge  an AI transition shot between two clips: approved-bridges ledger (EDL "approved_bridges_file" or
          --bridges) {bridge_id: value}, value as for drafts. When it is not approved (or the file is missing) the
          bridge and any transition items directly next to it are replaced by its "fallback" transition (default
          dissolve 0.6 s), so an EDL can name bridges before they exist.
  transition  joins the segments around it; two segments with nothing between them = cut.
          cut        hard cut (no overlap)
          dissolve   xfade=fade over dur          dip_black / dip_white   xfade=fadeblack / fadewhite over dur
          flare      bloom + brightness bump of length dur centred on the cut, with a "mix" s (default 0.125)
                     crossfade hidden under the peak; optional "gain" (default 1.0)
          whip       eased slide (custom xfade, "dir": left|right) over dur with a horizontal motion blur that
                     follows the slide speed; optional "blur" (default 1.0)
          A transition before the first segment = fade in from black (white for dip_white); after the last
          segment = fade out. Overlaps longer than the footage allows are clamped (warning).
  card    full-frame card ("fade": [in, out] s, default 0.6/0.6, fades from/to black); optional "mark" (a logo
          word set large in Latin caps), "note" (small print at the bottom; "\\n" = more lines), "preset".
          Missing zh/en/mark/style/note are filled from captions.json "cards" with the same id.
          title: [mark] main + secondary column; chapter: large tracked main line + small caps; end: accent rule,
          mark, main, secondary.

Captions: EDL "captions" merged with captions.json ({"captions": [...], "style": {...}, "cards": [...]} or a plain
  list); when both have captions for one anchor, captions.json wins for that anchor. Output start = first frame of
  the anchor segment + offset (/speed for a speed-changed clip). Offsets are measured from the clip's in-point
  ("trimmed", the default); with offset_basis "source" (top-level key of captions.json / the EDL, per caption, or
  --offset-basis) they are source times and the clip's in-trim is subtracted. "occurrence" picks the n-th use of an
  id (default 1); "at" gives an absolute output time. dur includes the fades.
  "zh" is the main line (any language), "en" the small secondary line in tracked caps. Each caption is typeset by
  typeset.py from the captions.json "style" (main line, optional channel glyph + hairline before '｜', tracked
  caps, ink light/dark, soft shadow, feathered scrim when the frame under a light caption is bright, style
  "chapter" = larger chapter line, "preset": "latin" for a Latin-script main line), overlaid with a smoothstep
  alpha fade (sendcmd -> colorchannelmixer) and a small rise during the fade-in.
  Type hierarchy (style "caption.levels", see typeset.py): the channel glyph is a large accent drop cap beside the
  main + secondary stack; caption "emph" (list of substrings of the line, or {"text", "level"} dicts, e.g. level
  "glyph" for an overview's 轻、静、快) sets key words larger and heavier, the rest of the line a step smaller in
  Light; "en_emph" substrings of the secondary line (product names) are set bold; optional "layout": "drop" |
  "inline" per caption; "zh_break" / "en_break" (substring or list) wrap the main line (after the glyph) / the
  secondary line after that substring, for a narrower block where the composition is tight. The secondary line
  tightens its tracking (down to style caption.en_fit.min_tracking_em) so it is never wider than the main line.
  An emph / break entry that is not in its line is reported as a warning. "pos" is a key of style "positions"
  (lower_left, lower_center, lower_right, upper_left, upper_center, upper_right, mid_left, mid_right, center) or a
  dict in reference px: one of x | cx | right and one of top | bottom | cy.
  "role": "label" (the opening AI label) or "note" (small print, usually with "preset": "note"), or "reading":
  false, marks a caption that is not reading text: it skips the min-gap, length and caption/callout overlap checks.
  Gaps are compared with one frame of tolerance (start and length are each rounded to frames).
  Safe box: style "safe_box" [x0, y0, x1, y1] (style px; the style preset "feed_safe" sets it for vertical frames):
  every caption block, callout label and dot and card small print outside it is a warning, also with --dry.
  Wording lint (EDL "lint"): every caption / callout / card line (mark, main, secondary, note) and the label is
  checked for words from a banned list (risky claims:
  medical, absolute, guarantee words; "banned" replaces the default list, "banned_extra" adds to it), numbers with
  % or degrees (claims that need a source), and length (max_cjk / max_latin characters for reading captions,
  max_callout_cjk / max_callout_latin for callout names; cards are not length-checked). Findings are warnings,
  never errors.
  The EDL "label" (if not empty) is drawn small in a corner of every frame.

Callouts (callouts.json, --callouts or EDL "callouts_file"): leader-line labels for exploded views, cutaways and
  part names. {"_style": {...overrides of the "callout" style in typeset.py...},
               "S03": {"at": 4.1, "stagger": 0.35, "fade_out": 0.5,
                       "items": [{"id": "lid", "order": 1, "anchor_xy": [x, y], "label_xy": [x, y],
                                  "zh": "...", "fn": "...", "en": "...", "ink"?: "auto|light|dark",
                                  "route"?: "auto|straight|elbow", "waypoints"?: [[x, y], ...]}]}}
  An item with a "todo" key has placeholder geometry (e.g. predicted before the shot's draft exists): it is drawn
  and a warning is printed until the key is removed.
  Coordinates are normalised 0-1 on the source frame (mapped through the clip's fit / crop): anchor_xy = the point
  on the part (dot), label_xy = where the leader ends; the label sits beyond it, left-aligned when it is right of
  the anchor, right-aligned when left, its first line centred on the leader. route auto = straight when level,
  else a diagonal plus a short horizontal shelf. Times ("at", "end", annotation "start") run on the CLIP CLOCK:
  source seconds of that clip (the same clock as in/out), continuing past "out" into hold_end (out + k s = k s
  into the freeze). Items start at at + (rank by "order") * stagger. The group ends (after fade_out) at "end", by
  default where the outgoing transition starts (callouts are overlays, so they must be gone before a dip/dissolve).
  Animation per item: dot pops in with an expanding ring, the hairline draws to the label (~0.3 s, ease-out), the
  label fades in gliding outward; all fade out together. Ink "auto" = light ink + soft shadow on dark backgrounds,
  dark ink on light (decided once per group from the median luma under its labels, so a set never mixes inks).
  Layout is checked (lines never cross, labels never overlap each other, a leader, the safe margin or the corner
  label) and reported as warnings. Rendered per frame with Pillow as an RGBA PNG sequence (cropped to the union
  box) and overlaid like the captions.

Pipeline: every clip/card is normalised once into a cached segment (<project>/.cache/edl_editor/<W>x<H>/): exact
frame count, target size and framing, constant fps, yuv444p tv-range bt709 (4:4:4 because xfade only works on
unsubsampled frames). One ffmpeg pass then joins the segments (concat / xfade), adds the flare/whip effects,
captions, callouts and label, and encodes H.264 High yuv420p tv-range bt709 (+faststart). Text never goes through
the filter graph as a string (Pillow PNGs) and ffmpeg runs inside the work directory with relative ASCII file
names, so CJK text and non-ASCII project paths need no escaping.
Writes <out>.mp4 and <out>.timeline.json (each item's start/end on the output timeline, captions, callouts,
bridge fallbacks, warnings). The output is silent: add music, sound and voice in a separate mix step.
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import typeset as TY  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
PROJECT = Path.cwd()               # set from --project in main()
VERSION = 'edl_editor/1'           # bump to invalidate cached segments

RES = {480: (854, 480), 720: (1280, 720), 1080: (1920, 1080)}
KINDS = ('cut', 'dissolve', 'dip_black', 'dip_white', 'flare', 'whip')
DEFAULT_DUR = {'cut': 0.0, 'dissolve': 0.6, 'dip_black': 0.8, 'dip_white': 0.8, 'flare': 0.5, 'whip': 0.4}
XFADE = {'dissolve': 'fade', 'dip_black': 'fadeblack', 'dip_white': 'fadewhite', 'flare': 'fade'}
FITS = ('cover', 'contain', 'blur')
MEDIA_EXT = ('.mov', '.mp4', '.m4v', '.mkv', '.webm', '.avi')
COLOR_TAGS = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv']
SETPARAMS = 'setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709'
TO_YUV = 'scale=out_color_matrix=bt709:out_range=tv'
# Wording lint: words that usually need legal / claims review in product advertising (medical, absolute,
# guarantee). A warning only; set EDL "lint": {"banned": [...]} to replace or {"banned_extra": [...]} to extend.
DEFAULT_LINT = {
    'banned': ['治疗', '治愈', '根治', '疗效', '诊断', '医疗级', '药用', '最佳', '最好', '最强', '最先进', '顶级',
               '首选', '国家级', '世界级', '永久', '零风险', '无副作用', '100%',
               'cure', 'treat', 'clinically', 'guarantee', 'best', 'no. 1', '#1', 'risk-free'],
    'numbers': True,          # flag numbers followed by % or degrees: claims that need a source
    'max_cjk': 12, 'max_latin': 42, 'max_callout_cjk': 14, 'max_callout_latin': 28,
}


# ----------------------------------------------------------------------------------------------- helpers

class Log:
    def __init__(self):
        self.warnings = []

    def warn(self, msg):
        self.warnings.append(msg)
        print('WARN', msg, flush=True)


def rel_root(p):
    p = Path(p)
    return p if p.is_absolute() else PROJECT / p


def short(p):
    """Path for messages: relative to the project when inside it."""
    try:
        return Path(p).resolve().relative_to(PROJECT.resolve()).as_posix()
    except ValueError:
        return str(p)


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


_GRAPH_OPT = None


def graph_opt():
    """ffmpeg >= 7 reads an option value from a file with -/option; older builds use -filter_complex_script."""
    global _GRAPH_OPT
    if _GRAPH_OPT is None:
        r = subprocess.run([FF, '-hide_banner', '-version'], capture_output=True, text=True, errors='replace')
        m = re.search(r'ffmpeg version n?(\d+)\.', r.stdout)
        _GRAPH_OPT = '-filter_complex_script' if (m and int(m.group(1)) < 7) else '-/filter_complex'
    return _GRAPH_OPT


def ffmpeg(args, cwd, quiet=True):
    cmd = [FF, '-y', '-hide_banner', '-loglevel', 'error' if quiet else 'warning',
           '-nostats' if quiet else '-stats', *map(str, args)]
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=quiet, text=True, encoding='utf-8', errors='replace')
    if r.returncode:
        raise RuntimeError(f'ffmpeg failed ({r.returncode}) in {cwd}:\n{r.stderr if quiet else ""}')


def probe(src):
    r = subprocess.run([FF, '-hide_banner', '-i', str(src)], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    err = r.stderr
    d = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err)
    v = re.search(r'Stream #\S+.*?Video: (.*)', err)
    if not (d and v):
        raise RuntimeError(f'cannot probe {src}')
    wh = re.search(r', (\d{2,5})x(\d{2,5})', v.group(1))
    fps = re.search(r'([\d.]+) fps', v.group(1))
    return dict(dur=int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)),
                w=int(wh.group(1)), h=int(wh.group(2)), fps=float(fps.group(1)) if fps else None)


def num(x, nd=6):
    if isinstance(x, float):
        s = f'{x:.{nd}f}'.rstrip('0').rstrip('.')
        return '0' if s in ('-0', '') else s
    return str(x)


def safe_id(s):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(s)) or 'seg'


def smoothstep(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


class Work:
    """Work directory where ffmpeg runs; image, command and graph files are referenced relative to it."""

    def __init__(self, path):
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)

    def rel(self, p):
        return Path(p).resolve().relative_to(self.dir.resolve()).as_posix()


# ----------------------------------------------------------------------------------------------- planning

def norm_transition(it, fps, log, where):
    kind = it.get('kind', 'cut')
    if kind not in KINDS:
        log.warn(f'{where}: unknown transition kind {kind!r} -> dissolve')
        kind = 'dissolve'
    dur = float(it.get('dur', DEFAULT_DUR[kind]))
    tr = dict(kind=kind, dur=dur)
    if kind == 'cut':
        tr['overlap'] = 0
    elif kind == 'flare':
        tr['mix'] = float(it.get('mix', 0.125))
        tr['gain'] = float(it.get('gain', 1.0))
        tr['overlap'] = max(0, round(tr['mix'] * fps))
        tr['dur'] = max(dur, 2.0 / fps)
    else:
        tr['overlap'] = max(1, round(dur * fps))
        if kind == 'whip':
            tr['dir'] = it.get('dir', 'left')
            tr['blur'] = float(it.get('blur', 1.0))
    return tr


def fit_scale(p, W, H, fit):
    """Scale factor from source px to output px for a fit mode."""
    return max(W / p['w'], H / p['h']) if fit == 'cover' else min(W / p['w'], H / p['h'])


def same_aspect(p, W, H):
    return abs((p['w'] / p['h']) / (W / H) - 1) < 0.01


def clip_segment(it, src, typ, fps, W, H, log, where):
    seg = dict(type=typ, id=it.get('id', '?'), src=None, slate=False)
    speed = float(it.get('speed', 1.0) or 1.0)
    if speed <= 0:
        raise SystemExit(f'{where}: speed must be > 0')
    seg.update(speed=speed, hold_start=float(it.get('hold_start', 0)), hold_end=float(it.get('hold_end', 0)))
    seg['interp'] = it.get('interp') or ('blend' if speed < 0.999 else 'dup')
    fit = it.get('fit', 'cover')
    if fit not in FITS:
        log.warn(f'{where} {seg["id"]}: unknown fit {fit!r} -> cover')
        fit = 'cover'
    focus = it.get('focus') or [0.5, 0.5]
    seg.update(fit=fit, focus=[min(1.0, max(0.0, float(v))) for v in focus[:2]])
    t_in = float(it.get('in', 0.0))
    out_end = it.get('out') is None or it.get('out') == 'end'
    if src is not None and Path(src).is_file():
        p = probe(src)
        t_out = p['dur'] if out_end else float(it['out'])
        if t_out > p['dur'] + 0.5 / fps:
            log.warn(f'{where} {seg["id"]}: out {t_out} beyond source end {p["dur"]:.3f}; last frame held')
        seg.update(src=str(Path(src).resolve()), probe=p, upscaled=fit_scale(p, W, H, fit) > 1.001)
    else:
        t_out = float((None if out_end else it.get('out')) or it.get('dur') or 4.0)
        seg.update(slate=True, probe=None, upscaled=False)
        log.warn(f'{where} {seg["id"]}: source missing ({short(src) if src else "no src, not in the ledger"}) '
                 f'-> black slate')
    if t_out <= t_in:
        raise SystemExit(f'{where} {seg["id"]}: out ({t_out}) must be after in ({t_in})')
    seg.update(**{'in': t_in, 'out': t_out})
    seg['frames'] = round(((t_out - t_in) / speed + seg['hold_start'] + seg['hold_end']) * fps)
    for k in ('callouts', 'annotations'):       # '_' keys are not part of the segment cache key
        if it.get(k):
            seg['_' + k] = it[k]
    return seg


def ledger_source(key, val, pattern):
    """Ledger value -> (path or None, task). value: a media path, a task id (through pattern, e.g.
    "gen/{id}/{task}"; extensions tried when the pattern has none), or {"task": ..., "src": ...}."""
    task, src = val, None
    if isinstance(val, dict):
        task, src = val.get('task'), val.get('src')
    elif isinstance(val, str) and (val.lower().endswith(MEDIA_EXT) or '/' in val or '\\' in val):
        task, src = Path(val).stem, val
    if src:
        return rel_root(src), task
    if not task:
        return None, None
    p = rel_root(pattern.format(id=key, task=task))
    if p.suffix.lower() in MEDIA_EXT:
        return p, task
    for ext in MEDIA_EXT:
        if p.with_name(p.name + ext).is_file():
            return p.with_name(p.name + ext), task
    return p.with_name(p.name + '.mp4'), task      # does not exist: reported by the caller


def build_plan(edl, cap_doc, W, H, style, log, basis_cli=None, ledgers=None, callout_doc=None, size_from=None):
    fps = int(edl.get('fps', 24))
    ledgers = ledgers or {}
    drafts = ledgers.get('drafts') or {}
    bridges_ok = ledgers.get('bridges') or {}
    pattern = edl.get('ledger_pattern') or 'gen/{id}/{task}'
    doc_cards = {c.get('id'): c for c in (cap_doc.get('cards', []) if isinstance(cap_doc, dict) else [])}
    card_style_key = hashlib.sha1(json.dumps([style.d.get('cards'), style.d.get('chapter'), style.d.get('min_px'),
                                              style.font_zh, style.font_en, style.font_glyph,
                                              TY.TYPE_VERSION], sort_keys=True,
                                             ensure_ascii=False).encode('utf-8')).hexdigest()[:10]
    segs, joins, fallbacks = [], [], []
    lead = pending = None
    for n, it in enumerate(edl.get('items', [])):
        where = f'item {n}'
        typ = it.get('type')
        if it.get('skip'):
            continue
        if typ == 'transition':
            tr = norm_transition(it, fps, log, where)
            if pending is not None and pending.get('bridge'):
                log.warn(f'{where}: {tr["kind"]} next to fallback bridge {pending["bridge"]} ignored (fallback used)')
                continue
            if pending is not None:
                log.warn(f'{where}: two transitions in a row; {pending["kind"]} replaced by {tr["kind"]}')
            pending = tr
            continue
        if typ == 'bridge':
            bid = it['id']
            src, task = (rel_root(it['src']), None) if it.get('src') else \
                ledger_source(bid, bridges_ok.get(bid), pattern)
            if src is not None and src.is_file():
                seg = clip_segment(it, src, 'bridge', fps, W, H, log, where)
                seg['task'] = task
            else:
                fb = norm_transition(it.get('fallback') or {'kind': 'dissolve', 'dur': 0.6}, fps, log, where)
                fb['bridge'] = bid
                if pending is not None:
                    log.warn(f'{where}: {pending["kind"]} before fallback bridge {bid} ignored (fallback used)')
                why = f'not in {ledgers.get("bridges_name") or "the bridges ledger"}' if src is None \
                    else f'file missing {short(src)}'
                fallbacks.append(dict(bridge=bid, reason=why, used=dict(kind=fb['kind'], dur=fb['dur'])))
                pending = fb
                continue
        elif typ == 'clip':
            src = it.get('src')
            if src:
                src = rel_root(src)
            elif it.get('id') in drafts:
                src, _ = ledger_source(it['id'], drafts[it['id']], pattern)
            seg = clip_segment(it, src, 'clip', fps, W, H, log, where)
        elif typ == 'card':
            ref = doc_cards.get(it.get('id'), {})
            dur = float(it.get('dur', ref.get('dur', 2.5)))
            fade = it.get('fade', ref.get('fade', [0.6, 0.6]))
            fade = [float(fade)] * 2 if isinstance(fade, (int, float)) else [float(x) for x in fade]
            fi, fo = (min(f, dur / 2.2) for f in fade)
            seg = dict(type='card', id=it.get('id', f'card{n}'), dur=dur, fade_in=fi, fade_out=fo,
                       frames=round(dur * fps), style_key=card_style_key,
                       **{k: it.get(k, ref.get(k, '' if k != 'style' else 'title'))
                          for k in ('style', 'mark', 'zh', 'en', 'note')})
            for k in ('mark_px', 'mark_tracking_em', 'zh_font', 'zh_px', 'zh_tracking_em', 'zh_ref', 'preset'):
                v = it.get(k, ref.get(k))
                if v is not None:
                    seg[k] = v
        else:
            log.warn(f'{where}: unknown item type {typ!r} skipped')
            continue
        if segs:
            joins.append(pending or dict(kind='cut', dur=0.0, overlap=0))
        elif pending is not None:
            lead = pending
        segs.append(seg)
        pending = None
    tail = pending
    if not segs:
        raise SystemExit('EDL has no clips/cards')
    media = [s for s in segs if s['type'] in ('clip', 'bridge') and s.get('probe')]
    other = [s for s in media if TY.orientation(s['probe']['w'], s['probe']['h']) != TY.orientation(W, H)]
    if media and len(other) * 2 > len(media):
        o = TY.orientation(other[0]['probe']['w'], other[0]['probe']['h'])
        msg = (f'{len(other)} of {len(media)} sources are {o} but the output is {W}x{H} ({TY.orientation(W, H)}, '
               f'{size_from or "?"}): they are reframed by "fit" (default cover = cropped)')
        if size_from == '--size':          # asked for on purpose (e.g. a vertical cut of a landscape film)
            print('NOTE', msg)
        else:
            log.warn(msg + '; set the EDL "size" to the delivery frame (e.g. [1080, 1920] for a vertical film) '
                     'or pass --size')

    prev = 0      # clamp overlaps to the footage (keep at least one clean frame of each segment)
    for i, j in enumerate(joins):
        a, b = segs[i], segs[i + 1]
        room = min(a['frames'] - prev - 1, b['frames'] - 1)
        if j['overlap'] > room:
            log.warn(f'{j["kind"]} {a["id"]}->{b["id"]}: overlap {j["overlap"]} f clamped to {max(0, room)} f')
            j['overlap'] = max(0, room)
        prev = j['overlap']

    t = 0         # output timeline in frames
    for i, s in enumerate(segs):
        s['start'], s['end'] = t, t + s['frames']
        if i < len(joins):
            j = joins[i]
            j['start'], j['end'] = s['end'] - j['overlap'], s['end']
            j['a'], j['b'] = s['id'], segs[i + 1]['id']
            t = j['start']
    total = segs[-1]['end']
    for j in joins:                        # effect windows in seconds
        if j['kind'] == 'flare':
            c = (j['start'] + (j['overlap'] - 1) / 2) / fps
            j['fx'] = (c - j['dur'] / 2, c + j['dur'] / 2, c)
        elif j['kind'] == 'whip' and j['overlap'] > 0:
            j['fx'] = (j['start'] / fps, j['end'] / fps)
    lead = None if lead is not None and lead['kind'] == 'cut' else lead
    tail = None if tail is not None and tail['kind'] == 'cut' else tail
    lint_cfg = dict(DEFAULT_LINT, **{k: v for k, v in (edl.get('lint') or {}).items() if k != 'banned_extra'})
    lint_cfg['banned'] = list(lint_cfg['banned']) + list((edl.get('lint') or {}).get('banned_extra') or [])
    for s in segs:              # cards carry the product name, slogan and small print: same wording lint, no length
        if s['type'] == 'card':
            main = ' '.join(x for x in (s.get('mark'), s.get('zh')) if x)
            sub = ' '.join(x for x in (s.get('en'), s.get('note')) if x)
            lint(main, sub, f'card {s["id"]}', log, lint_cfg, 10 ** 6, 10 ** 6)
    if edl.get('label'):
        lint(edl['label'], '', 'label', log, lint_cfg, 10 ** 6, 10 ** 6)
    plan = dict(fps=fps, W=W, H=H, segs=segs, joins=joins, lead=lead, tail=tail, total=total,
                fallbacks=fallbacks, label=edl.get('label', ''), lint=lint_cfg)
    plan['captions'] = resolve_captions(edl, cap_doc, plan, style, log, basis_cli)
    plan['callouts'] = resolve_callouts(plan, callout_doc, style, log)
    return plan


def clip_clock(seg, s, fps):
    """Output seconds of clip-clock time s: source seconds of the clip (same clock as in/out); past 'out' it runs
    on into the hold_end freeze, before 'in' back into hold_start."""
    base = seg['start'] / fps + seg.get('hold_start', 0.0)
    sp = seg.get('speed', 1.0) or 1.0
    if s <= seg['out']:
        t = base + (s - seg['in']) / sp
    else:
        t = base + (seg['out'] - seg['in']) / sp + (s - seg['out'])
    return min(max(t, seg['start'] / fps), seg['end'] / fps)


def norm_to_out(seg, plan, xy):
    """Normalised 0-1 source-frame point -> output px (follows the clip's fit: cover crop around focus, or a
    centred contain / blur frame)."""
    W, H = plan['W'], plan['H']
    p = seg.get('probe')
    x, y = float(xy[0]), float(xy[1])
    if not p or same_aspect(p, W, H):
        return (x * W, y * H)
    fit = seg.get('fit', 'cover')
    k = fit_scale(p, W, H, fit)
    sw, sh = p['w'] * k, p['h'] * k
    if fit == 'cover':
        fx, fy = seg.get('focus') or (0.5, 0.5)
        return (x * sw - (sw - W) * fx, y * sh - (sh - H) * fy)
    return (x * sw + (W - sw) / 2, y * sh + (H - sh) / 2)


def resolve_callouts(plan, doc, style, log):
    """Clip 'callouts' / 'annotations' -> timed callout groups (one per clip segment) in output px."""
    fps, segs, joins = plan['fps'], plan['segs'], plan['joins']
    groups_doc = {k: v for k, v in (doc or {}).items() if not str(k).startswith('_') and isinstance(v, dict)}
    T = style.d['callout'].get('timing') or {}
    L = plan['lint']
    out = []

    def find(ref, seg_id):
        if ':' in ref or '/' in ref:
            gid, iid = re.split(r'[:/]', ref, maxsplit=1)
            cands = [(gid, groups_doc.get(gid, {}))]
        else:
            iid = ref
            cands = [(seg_id, groups_doc.get(seg_id, {}))] + [(k, v) for k, v in groups_doc.items() if k != seg_id]
        for gid, g in cands:
            for it in g.get('items', []):
                if it.get('id') == iid:
                    return gid, g, it
        return None, None, None

    for si, seg in enumerate(segs):
        co, ann = seg.get('_callouts'), seg.get('_annotations')
        if not co and not ann:
            continue
        if doc is None:
            log.warn(f'{seg["id"]}: callouts requested but no callouts file was given (EDL "callouts_file" or '
                     f'--callouts); skipped')
            continue
        specs, end, fade_out, gdoc = [], None, None, {}
        if co:
            co = {'group': co} if isinstance(co, str) else ({'group': seg['id']} if co is True else dict(co))
            gid = co.get('group', seg['id'])
            gdoc = groups_doc.get(gid)
            if gdoc is None:
                log.warn(f'{seg["id"]}: callout group {gid!r} not in the callouts file; skipped')
                gdoc = {}
            else:
                at = co.get('at', gdoc.get('at'))
                at = seg['out'] if at in (None, 'hold') else float(at)
                stag = float(co.get('stagger', gdoc.get('stagger', T.get('stagger_s', 0.35))))
                items = [dict(it) for it in gdoc.get('items', []) if not it.get('skip')]
                if co.get('items'):
                    items = [it for it in items if it.get('id') in set(co['items'])]
                items = [it for _, it in sorted(enumerate(items), key=lambda e: (e[1].get('order', 1e9), e[0]))]
                for k, it in enumerate(items):
                    specs.append(dict(it, _start=at + k * stag, _group=gid))
            end, fade_out = co.get('end', gdoc.get('end')), co.get('fade_out', gdoc.get('fade_out'))
        for a in ann or []:
            gid, g, it = find(str(a.get('ref', '')), seg['id'])
            if it is None:
                log.warn(f'{seg["id"]}: annotation ref {a.get("ref")!r} not found in the callouts file; skipped')
                continue
            specs.append(dict(it, **{k: v for k, v in a.items() if k not in ('ref', 'start')},
                              _start=float(a.get('start', g.get('at', seg['out']))), _group=gid))
            if end is None and a.get('end') is not None:
                end = a['end']
        uniq = {}                     # an item named by both "callouts" and "annotations" is drawn once (annotation wins)
        for sp in specs:
            uniq[(sp['_group'], sp.get('id'))] = sp
        if len(uniq) < len(specs):
            log.warn(f'{seg["id"]}: {len(specs) - len(uniq)} callout item(s) requested twice (callouts + '
                     f'annotations); drawn once')
        specs = []
        for sp in uniq.values():
            if not (isinstance(sp.get('anchor_xy'), (list, tuple)) and isinstance(sp.get('label_xy'), (list, tuple))):
                log.warn(f'{seg["id"]}: callout {sp["_group"]}:{sp.get("id")} has no anchor_xy/label_xy yet; skipped')
                continue
            if sp.get('todo'):            # placeholder geometry (e.g. predicted before the shot's draft exists)
                log.warn(f'{seg["id"]}: callout {sp["_group"]}:{sp.get("id")} position is a placeholder (todo); '
                         f're-measure it on the approved draft')
            specs.append(sp)
        if not specs:
            continue
        t_items = [clip_clock(seg, sp['_start'], fps) for sp in specs]
        if end is not None:
            t_end = clip_clock(seg, float(end), fps)
        else:
            t_end = seg['end'] / fps
            j = joins[si] if si < len(joins) else None
            if j and j['kind'] != 'cut' and j['overlap'] > 0:
                # overlays sit above the transition: be gone when the picture under them is. xfade fadeblack/white
                # takes the outgoing shot to black within the first 20 % of the overlap; a dissolve may carry the
                # callouts 0.2 s in (caption rule); flare/whip: before the effect window
                w0 = join_window(j, fps)[0]
                extra_s = {'dip_black': 0.2 * j['overlap'] / fps, 'dip_white': 0.2 * j['overlap'] / fps,
                           'dissolve': min(0.2, j['overlap'] / fps / 2)}.get(j['kind'], 0.0)
                t_end = min(t_end, w0 + extra_s)
            if si == len(segs) - 1 and plan.get('tail'):            # film fade-out happens under the overlays
                t_end = min(t_end, plan['total'] / fps - plan['tail']['dur'])
        f0 = round(min(t_items) * fps)
        nf = round(t_end * fps) - f0
        if nf < 2:
            log.warn(f'{seg["id"]}: callouts start after their end ({min(t_items):.2f}s >= {t_end:.2f}s); skipped')
            continue
        last_in = max(t_items) - f0 / fps
        full = nf / fps - last_in - float(T.get('line_delay_s', 0.06)) - float(T.get('text_delay_s', 0.26)) \
            - float(T.get('text_fade_s', 0.4)) - float(fade_out or T.get('fade_out_s', 0.5))
        if full < 1.5:
            log.warn(f'{seg["id"]}: callouts hold their full state only {full:.2f}s (< 1.5 s to read); '
                     f'lengthen hold_end or start earlier')
        items = []
        for sp, ti in zip(specs, t_items):
            A, Lp = norm_to_out(seg, plan, sp['anchor_xy']), norm_to_out(seg, plan, sp['label_xy'])
            d = dict(id=sp.get('id', '?'), group=sp['_group'], A=A, L=Lp, zh=sp.get('zh', ''), fn=sp.get('fn', ''),
                     en=sp.get('en', ''), ink=sp.get('ink', 'auto'), route=sp.get('route', 'auto'),
                     order=sp.get('order'), t0=ti - f0 / fps, clock=sp['_start'])
            if sp.get('waypoints'):
                d['pts'] = [A] + [norm_to_out(seg, plan, w) for w in sp['waypoints']] + [Lp]
            lint(d['zh'], d['fn'] + ' ' + d['en'], f'callout {d["id"]}', log, L,
                 L['max_callout_cjk'], L['max_callout_latin'])
            items.append(d)
        out.append(dict(id=seg['id'], seg_index=si, f0=f0, nf=nf, start=f0 / fps, end=(f0 + nf) / fps,
                        dur=nf / fps, fade_out=fade_out, items=items, full_state_s=round(full, 2),
                        group_ids=sorted({i['group'] for i in items})))
    for g in out:                           # keep captions and callouts from talking over each other
        for c in plan.get('captions', []):
            if not c.get('reading', True):
                continue
            ov = min(g['end'], c['end']) - max(g['start'], c['start'])
            if ov > 0:
                log.warn(f'caption {c["id"]} overlaps the {g["id"]} callouts by {ov:.2f}s')
    return out


def resolve_captions(edl, cap_doc, plan, style, log, basis_cli):
    fps, segs, joins, total = plan['fps'], plan['segs'], plan['joins'], plan['total']
    L = plan['lint']
    edl_caps = [c for c in (edl.get('captions', []) or []) if not c.get('skip')]
    is_doc = isinstance(cap_doc, dict)
    doc_caps = [c for c in (cap_doc.get('captions', []) if is_doc else (cap_doc or [])) if not c.get('skip')]
    aid = lambda c: c.get('anchor', c.get('shot'))
    doc_anchors = {aid(c) for c in doc_caps}
    dropped = sorted({str(aid(c)) for c in edl_caps if aid(c) in doc_anchors})
    if dropped:
        print(f'NOTE captions file overrides the EDL captions for anchors {dropped}')
    doc_basis = (cap_doc.get('offset_basis') if is_doc else None) or 'trimmed'
    edl_basis = edl.get('caption_offset_basis', edl.get('offset_basis')) or 'trimmed'
    caps = [dict(c, _origin='captions_file', _basis=c.get('offset_basis', doc_basis)) for c in doc_caps] + \
           [dict(c, _origin='edl', _basis=c.get('offset_basis', edl_basis)) for c in edl_caps
            if aid(c) not in doc_anchors]
    occ = {}
    for s in segs:
        occ.setdefault(s['id'], []).append(s)
    fb = {j['bridge']: j for j in joins if j.get('bridge')}
    out = []
    for c in caps:
        anchor = aid(c)
        basis = basis_cli or c['_basis']
        off = float(c.get('offset', 0.0))
        dur = float(c.get('dur', 3.0))
        seg = None
        if c.get('at') is not None:
            t0 = float(c['at'])
        elif anchor in occ:
            k = int(c.get('occurrence', 1))
            lst = occ[anchor]
            if k > len(lst):
                log.warn(f'caption {anchor}#{k}: only {len(lst)} occurrence(s); using the last')
            seg = lst[min(k, len(lst)) - 1]
            if basis == 'source' and seg['type'] != 'card':
                off -= seg.get('in', 0.0)
            t0 = seg['start'] / fps + seg.get('hold_start', 0) + off / seg.get('speed', 1.0)
            if t0 + dur > seg['end'] / fps + 1e-6:
                print(f'NOTE caption {anchor} +{off}s runs {t0 + dur - seg["end"] / fps:.2f}s past its segment end')
        elif anchor in fb:
            t0 = fb[anchor]['start'] / fps + off
            log.warn(f'caption anchored to fallback bridge {anchor}: placed at the fallback transition start')
        else:
            log.warn(f'caption anchor {anchor!r} not on the timeline; caption skipped ({c.get("zh", "")})')
            continue
        f0, nf = round(t0 * fps), max(2, round(dur * fps))
        if f0 < 0 or f0 + nf > total:
            log.warn(f'caption {anchor} [{f0 / fps:.2f},{(f0 + nf) / fps:.2f}] clipped to the film')
            f0 = max(0, f0)
            nf = max(2, min(nf, total - f0))
        zh, en = c.get('zh', '') or '', c.get('en', '') or ''
        reading = is_reading(c)
        lint(zh, en, f'caption {c.get("id", anchor)}', log, L, L['max_cjk'] if reading else 10 ** 6,
             L['max_latin'] if reading else 10 ** 6)
        out.append(dict(id=c.get('id', anchor), anchor=anchor, origin=c['_origin'], f0=f0, nf=nf, reading=reading,
                        role=c.get('role'),
                        start=f0 / fps, end=(f0 + nf) / fps, zh=zh, en=en, pos=c.get('pos', 'lower_left'),
                        ink=c.get('ink', 'light'), style=c.get('style'), fade=c.get('fade'),
                        emph=c.get('emph') or [], en_emph=c.get('en_emph') or [], layout=c.get('layout'),
                        zh_break=c.get('zh_break'), en_break=c.get('en_break'), preset=c.get('preset'),
                        seg_index=segs.index(seg) if seg is not None else None))
    out.sort(key=lambda c: c['start'])
    trim_caption_ends(out, joins, fps, style, total)
    gap = float(style.d.get('min_gap_s', 0) or 0)
    reading = [c for c in out if c['reading']]        # labels and small print are not reading text
    for a, b in zip(reading, reading[1:]):
        # start and length are each rounded to whole frames: allow one frame before calling a gap short
        if b['start'] < a['end'] + gap - 1.0 / fps - 1e-6:
            log.warn(f'captions {a["id"]} and {b["id"]} are {b["start"] - a["end"]:.2f}s apart (min gap {gap}s)')
    for c in out:          # rule: a caption may overlap a transition by at most 0.2 s
        for j in joins:
            if j['kind'] == 'cut' or c.get('style') == 'chapter':
                continue
            w0, w1 = join_window(j, fps)
            ov = min(c['end'], w1) - max(c['start'], w0)
            if ov > 0.2 + 0.5 / fps:            # half a frame of rounding tolerance
                log.warn(f'caption {c["id"]} overlaps the {j["kind"]} {j["a"]}->{j["b"]} by {ov:.2f}s (> 0.2s)')
    return out


def is_reading(c):
    """False for captions that are not reading text: "reading": false, "role": "label" (the opening AI label) or
    "note" (small print), or preset "note". They skip the gap, length and caption/callout overlap checks; the
    wording lint still runs."""
    if c.get('reading') is not None:
        return bool(c['reading'])
    return c.get('role') not in ('label', 'note') and c.get('preset') != 'note'


def join_window(j, fps):
    """Seconds during which a transition changes the picture (effect window for flare/whip, else the overlap)."""
    return (j['fx'][0], j['fx'][1]) if j.get('fx') else (j['start'] / fps, j['end'] / fps)


def trim_caption_ends(caps, joins, fps, style, total):
    """Transition rule (style "transition_trim"): a caption may run into a transition that starts after it by at
    most max_s (0.2 s); otherwise its END is pulled in (never its start), keeping at least min_on_s (2.0 s) on
    screen. Chapter captions are exempt (a chapter title may ride the chapter's own dissolve). Fills c['trimmed']."""
    rule = style.d.get('transition_trim') or {}
    max_ov, min_on = float(rule.get('max_s', 0.2)), float(rule.get('min_on_s', 2.0))
    for c in caps:
        if c.get('style') == 'chapter':
            continue
        for j in joins:
            if j['kind'] == 'cut':
                continue
            w0, _ = join_window(j, fps)
            if w0 <= c['start'] + 1e-6 or c['end'] - w0 <= max_ov + 1e-6:
                continue
            f_end = max(math.floor((w0 + max_ov) * fps + 1e-6), c['f0'] + round(min_on * fps))
            nf = max(2, min(c['nf'], f_end - c['f0'], total - c['f0']))
            if nf < c['nf']:
                old = c['end']
                c['nf'], c['end'] = nf, (c['f0'] + nf) / fps
                c['trimmed'] = round(old - c['end'], 3)
                print(f'NOTE caption {c["id"]} end trimmed {c["trimmed"]:.2f}s for the {j["kind"]} {j["a"]}->{j["b"]}')


def lint(main, sub, where, log, cfg, max_cjk, max_latin):
    """Wording check (warnings only): banned words, numbers with %/degrees, line length."""
    text = f'{main} {sub}'
    low = text.lower()
    hit = [w for w in cfg.get('banned') or [] if w and w.lower() in low]
    nums = cfg.get('numbers', True) and re.search(r'\d+(\.\d+)?\s*(%|℃|°|度)', text)
    if hit or nums:
        log.warn(f'{where}: wording to check against your claim rules: {hit or "number with %/°"} in '
                 f'{main!r} {sub!r}')
    body = main.split('｜', 1)[-1].split('|', 1)[-1]
    cjk = len(re.findall(r'[぀-ヿ㐀-鿿가-힯]', body))
    if cjk and cjk + len(re.findall(r'[A-Za-z0-9]', body)) > max_cjk:
        log.warn(f'{where}: main line is longer than {max_cjk} CJK characters: {main!r}')
    elif not cjk and len(body) > max_latin:
        log.warn(f'{where}: main line is longer than {max_latin} characters: {main!r}')


# ----------------------------------------------------------------------------------------------- segments

def seg_key(seg, plan):
    d = {k: v for k, v in seg.items() if k not in ('start', 'end', 'probe', 'file') and not k.startswith('_')}
    if seg.get('src'):
        st = Path(seg['src']).stat()
        d['src_stat'] = [st.st_size, int(st.st_mtime)]
    d.update(v=VERSION, W=plan['W'], H=plan['H'], fps=plan['fps'])
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:12]


def scale_cover(p, W, H, focus=(0.5, 0.5)):
    base = f'scale={W}:{H}:flags=lanczos:out_range=tv:out_color_matrix=bt709'
    if same_aspect(p, W, H):
        return base
    fx, fy = focus
    return (f'scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos:out_range=tv:out_color_matrix=bt709,'
            f'crop=w={W}:h={H}:x=(iw-ow)*{num(float(fx))}:y=(ih-oh)*{num(float(fy))}')


def scale_contain(p, W, H):
    return (f'scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos:out_range=tv:'
            f'out_color_matrix=bt709')


def run_graph(graph, inputs, nframes, fps, path, work, tag):
    gfile = work.dir / f'graph_{safe_id(tag)}.txt'
    gfile.write_text(graph, encoding='utf-8')
    tmp = path.with_name(path.stem + '.part.mp4')
    ffmpeg([*inputs, graph_opt(), gfile.name, '-map', '[out]', '-frames:v', str(nframes), '-r', str(fps),
            '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '8', '-pix_fmt', 'yuv444p', '-g', '48',
            *COLOR_TAGS, str(tmp)], work.dir)
    os.replace(tmp, path)


def render_segment(seg, plan, style, cache, fresh):
    key = seg_key(seg, plan)
    path = cache / f'{safe_id(seg["id"])}_{key}.mp4'
    seg['file'] = str(path)
    if path.exists() and not fresh:
        return seg['id'], 'cached'
    fps, W, H, n = plan['fps'], plan['W'], plan['H'], seg['frames']
    work = Work(cache / 'seg_work' / f'{safe_id(seg["id"])}_{key}')
    if seg['type'] == 'card' or seg['slate']:
        if seg['type'] == 'card':
            info = TY.card_png(seg, style, work.dir / 'card.png')
            if info.get('fitted'):
                print(f'NOTE card {seg["id"]}: line(s) scaled {info["fitted"]} to fit the frame width', flush=True)
            fi, fo = seg['fade_in'], seg['fade_out']
        else:
            TY.slate_png(f'{seg["id"]}（素材缺失 · missing source）', style, work.dir / 'card.png')
            fi = fo = 0
        fades = [f"fade=t=in:st=0:d={num(fi)}"] if fi > 0 else []
        if fo > 0:
            fades.append(f"fade=t=out:st={num(n / fps - fo)}:d={num(fo)}")
        graph = ','.join(['[0:v]format=rgb24', TO_YUV, 'format=yuv444p,setsar=1', *fades, SETPARAMS]) + '[out]'
        inputs = ['-loop', '1', '-framerate', str(fps), '-t', num(n / fps + 1), '-i', 'card.png']
    else:
        sp, p = seg['speed'], seg['probe']
        ch = ['[0:v]setpts=PTS-STARTPTS', f"trim=start={num(seg['in'])}:end={num(seg['out'])}",
              'setpts=PTS-STARTPTS']
        if abs(sp - 1) > 1e-6:
            ch.append(f'setpts=PTS/{num(sp)}')
            if seg['interp'] == 'mci':
                ch.append(f'minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1')
            elif seg['interp'] == 'blend':
                ch.append(f'framerate=fps={fps}')
        ch.append(f'fps={fps}')
        post = ['format=yuv444p,setsar=1']
        if seg['hold_start'] > 0:
            post.append(f"tpad=start_mode=clone:start_duration={num(seg['hold_start'])}")
        post += [f"tpad=stop_mode=clone:stop_duration={num(seg['hold_end'] + 1.0)}", SETPARAMS]
        fit = seg.get('fit', 'cover')
        if fit == 'cover' or same_aspect(p, W, H):
            graph = ','.join(ch + [scale_cover(p, W, H, seg.get('focus') or (0.5, 0.5))] + post) + '[out]'
        elif fit == 'contain':
            graph = ','.join(ch + [scale_contain(p, W, H), 'format=yuv444p',
                                   f'pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black'] + post) + '[out]'
        else:                 # blur: the whole picture over a blurred, darkened cover fill of itself
            sig = round(0.025 * max(W, H), 2)
            graph = (','.join(ch) + ',split[bga][fga];'
                     f'[bga]{scale_cover(p, W, H)},format=yuv444p,gblur=sigma={sig}:steps=3,'
                     f'eq=brightness=-0.06:saturation=0.85[bg];'
                     f'[fga]{scale_contain(p, W, H)},format=yuv444p[fg];'
                     f'[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2:format=yuv444,' + ','.join(post) + '[out]')
        inputs = ['-i', seg['src']]
    run_graph(graph, inputs, n, fps, path, work, seg['id'])
    return seg['id'], 'rendered'


# ----------------------------------------------------------------------------------------------- typography

def luma_probe(plan, work):
    """luma(t, seg, box) -> mean Rec.709 luma 0..1 of the composite-to-be at time t inside box (output px),
    read from the normalised segment that shows that time (the anchor segment when it covers t)."""
    import numpy as np
    from PIL import Image
    fps, cache = plan['fps'], {}
    (work.dir / 'luma').mkdir(exist_ok=True)

    def f(t, seg, box):
        fr = int(round(t * fps))
        if seg is None or not (seg['start'] <= fr < seg['end']):
            seg = next((s for s in plan['segs'] if s['start'] <= fr < s['end']), plan['segs'][-1])
        k = min(seg['frames'] - 1, max(0, fr - seg['start']))
        key = (seg['file'], k)
        if key not in cache:
            png = work.dir / 'luma' / f'{Path(seg["file"]).stem}_{k:04d}.png'
            if not png.exists():
                ffmpeg(['-ss', num(k / fps), '-i', seg['file'], '-frames:v', '1', '-update', '1', png.name],
                       png.parent)
            cache[key] = np.asarray(Image.open(png).convert('RGB'), dtype=np.float32) / 255.0
        a = cache[key]
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(a.shape[1], x1), min(a.shape[0], y1)
        if x1 <= x0 or y1 <= y0:
            return None
        r = a[y0:y1, x0:x1]
        return float((r[..., 0] * 0.2126 + r[..., 1] * 0.7152 + r[..., 2] * 0.0722).mean())
    return f


def typeset(plan, style, work, log=None):
    luma = luma_probe(plan, work)
    (work.dir / 'cap').mkdir(exist_ok=True)
    for f in (work.dir / 'cap').glob('*'):
        f.unlink()
    if plan['label']:
        plan['label_info'] = TY.label_png(plan['label'], style, work.dir / 'cap' / 'label.png')
    zone = None
    if plan.get('label_info'):
        L = plan['label_info']
        zone = (L['x'] - 6, L['y'] - 6, L['x'] + L['w'] + 6, L['y'] + L['h'] + 6)
    fps = plan['fps']
    for k, g in enumerate(plan.get('callouts', [])):
        seg = plan['segs'][g['seg_index']]
        t_full = min(g['end'] - 0.6, g['start'] + max(i['t0'] for i in g['items']) + 0.8)
        info = TY.callout_frames(g, style, work.dir / 'co' / f'g{k:02d}', fps,
                                 luma=lambda box, t=t_full, seg=seg: luma(t, seg, box), label_zone=zone)
        for w in info['warnings']:
            (log.warn if log else print)(f'{g["id"]} {w}')
        g['ty'] = info
        print(f'CALLOUTS {g["id"]}: {len(g["items"])} items, {info["n"]} frames, box {info["w"]}x{info["h"]} '
              f'at {info["x"]},{info["y"]}; full state {g["full_state_s"]}s', flush=True)
    for k, c in enumerate(plan['captions']):
        seg = plan['segs'][c['seg_index']] if c['seg_index'] is not None else None
        mid = (c['start'] + c['end']) / 2
        info = TY.caption_png(c, style, work.dir / 'cap' / f'c{k:02d}.png',
                              luma=lambda box, mid=mid, seg=seg: luma(mid, seg, box))
        if c.get('fade') is not None:
            fd = c['fade']
            info['fade'] = (float(fd), float(fd)) if isinstance(fd, (int, float)) else tuple(map(float, fd))
        if info.get('missing_emph'):
            (log.warn if log else print)(f'caption {c["id"]}: emph / en_emph / break {info["missing_emph"]} not found '
                                         f'in its line')
        if info.get('fit_scale'):
            print(f'NOTE caption {c["id"]} scaled to {info["fit_scale"]} to fit the frame width '
                  f'(add zh_break / en_break for a narrower block)')
        c['ty'] = info


def safe_box_check(plan, style, log):
    """Style "safe_box" [x0, y0, x1, y1] (px at a 1080 px short side, scaled like every style px value; the preset
    "feed_safe" sets it for vertical frames): every caption block, callout label and dot, and card small print must
    lie inside it. Layout only (no rendering), so it also runs with --dry. Returns a summary for the timeline."""
    box = style.d.get('safe_box')
    if not box:
        return None
    x0, y0, x1, y1 = (float(v) * style.s for v in box)
    fmt = lambda b: '[' + ', '.join(str(round(v)) for v in b) + ']'
    inside = lambda b: b[0] >= x0 - 1 and b[1] >= y0 - 1 and b[2] <= x1 + 1 and b[3] <= y1 + 1
    out, n = [], 0
    for c in plan['captions']:
        bx, by, bw, bh = TY.caption_box(c, style)
        b, n = (bx, by, bx + bw, by + bh), n + 1
        if not inside(b):
            out.append(f'caption {c["id"]}: block {fmt(b)}')
    for g in plan.get('callouts', []):
        grp = dict(items=[dict(i) for i in g['items']])
        TY.callout_layout(grp, style)
        for it in grp['items']:
            n += 1
            if not inside(it['text_box']):
                out.append(f'callout {g["id"]}:{it["id"]}: label {fmt(it["text_box"])}')
            if not inside((*it['A'], *it['A'])):
                out.append(f'callout {g["id"]}:{it["id"]}: dot at {fmt(it["A"])}')
    for seg in plan['segs']:
        nb = TY.card_note_box(seg, style) if seg['type'] == 'card' else None
        if nb:
            n += 1
            if not inside(nb):
                out.append(f'card {seg["id"]}: small print {fmt(nb)}')
    for o in out:
        log.warn(f'{o} is outside the safe box {fmt((x0, y0, x1, y1))}')
    print(f'SAFE BOX {fmt((x0, y0, x1, y1))}: {n} block(s) checked, {len(out)} outside')
    return dict(box=[round(v, 1) for v in (x0, y0, x1, y1)], checked=n, outside=out)


# ----------------------------------------------------------------------------------------------- final pass

def whip_expr(direction):
    """Custom xfade: eased (smootherstep) slide. P runs 1 -> 0 in xfade, so q = 1 - P.
    No st()/ld(): xfade evaluates the expression from several slice threads sharing one register set."""
    q = '(1-P)'
    s = f'({q}*{q}*{q}*({q}*({q}*6-15)+10)*W)'
    pa = 'if(eq(PLANE,0),a0({x},Y),if(eq(PLANE,1),a1({x},Y),a2({x},Y)))'
    pb = 'if(eq(PLANE,0),b0({x},Y),if(eq(PLANE,1),b1({x},Y),b2({x},Y)))'
    if direction == 'right':
        return f'if(gte(X-{s},0),{pa.format(x=f"X-{s}")},{pb.format(x=f"X-{s}+W")})'
    return f'if(lt(X+{s},W),{pa.format(x=f"X+{s}")},{pb.format(x=f"X+{s}-W")})'


def fx_graph(vin, j, k, plan):
    """Effect on the joined stream inside the transition's window (global time T / t)."""
    W, H = plan['W'], plan['H']
    if j['kind'] == 'whip':
        t0, t1 = j['fx']
        u = f'clip((T-{num(t0)})/{num(t1 - t0)},0,1)'
        sig = round(0.045 * W * j.get('blur', 1.0), 2)          # motion blur follows the slide (frame width)
        en = f"enable='between(t,{num(t0 - 0.001)},{num(t1)})'"
        return (f'[{vin}]split[w{k}a][w{k}b];[w{k}b]gblur=sigma={sig}:sigmaV=0:steps=2:{en}[w{k}g];'
                f"[w{k}a][w{k}g]blend=all_expr='A+(B-A)*min(1,18.4*{u}*{u}*(1-{u})*(1-{u}))':{en}[fx{k}]")
    t0, t1, c = j['fx']
    hw = (t1 - t0) / 2
    w = f'pow(cos(PI/2*clip((T-{num(c)})/{num(hw)},-1,1)),2)'
    g = j.get('gain', 1.0)
    glow, flash, desat = 0.9 * g, 0.32 * g, 0.55
    en = f"enable='between(t,{num(t0)},{num(t1)})'"
    lift = f'({w}*({num(glow)}*max(B-16,0)+{num(flash)}*219))'      # screen-add the bloom (no st/ld: threads)
    c0 = f'clip(A+{lift}-A*{lift}/255,16,235)'
    c1 = f'128+(A-128)*(1-{desat}*{w})-6*{w}'
    c2 = f'128+(A-128)*(1-{desat}*{w})+7*{w}'
    bloom = round(0.0533 * min(W, H), 2)                       # bloom radius follows the frame's short side
    return (f'[{vin}]split[f{k}a][f{k}b];[f{k}b]gblur=sigma={bloom}:steps=2:{en}[f{k}g];'
            f"[f{k}a][f{k}g]blend=c0_expr='{c0}':c1_expr='{c1}':c2_expr='{c2}':{en}[fx{k}]")


def final_graph(plan, work):
    """Returns (graph text, extra input args)."""
    fps, total = plan['fps'], plan['total']
    segs, joins = plan['segs'], plan['joins']
    g = [f'[{i}:v]settb=1/{fps},setpts=N,fps={fps},format=yuv444p[s{i}]' for i in range(len(segs))]
    cur = 's0'
    for i, j in enumerate(joins):
        out = f'j{i}'
        if j['overlap'] == 0:
            g.append(f'[{cur}][s{i + 1}]concat=n=2:v=1:a=0,settb=1/{fps},setpts=N,fps={fps}[{out}]')
        else:
            if j['kind'] == 'whip':
                tr = f"transition=custom:expr='{whip_expr(j.get('dir', 'left'))}'"
            else:
                tr = f"transition={XFADE[j['kind']]}"
            g.append(f'[{cur}][s{i + 1}]xfade={tr}:duration={num(j["overlap"] / fps)}:offset={num(j["start"] / fps)},'
                     f'settb=1/{fps},setpts=N,fps={fps}[{out}]')
        cur = out
    k = 0
    for j in joins:
        if j.get('fx'):
            g.append(fx_graph(cur, j, k, plan))
            cur, k = f'fx{k}', k + 1
    post = []
    for edge, tr in (('in', plan['lead']), ('out', plan['tail'])):
        if tr:
            col = 'white' if tr['kind'] == 'dip_white' else 'black'
            d = max(tr['dur'], 1 / fps)
            st = 0 if edge == 'in' else total / fps - d
            post.append(f'fade=t={edge}:st={num(st)}:d={num(d)}:color={col}')
    if post:
        g.append(f'[{cur}]' + ','.join(post) + '[faded]')
        cur = 'faded'

    extra, idx = [], len(segs)
    (work.dir / 'cmd').mkdir(exist_ok=True)
    for k, c in enumerate(plan['captions']):
        info = c['ty']
        fi, fo = info['fade']
        nfi, nfo = max(1, round(fi * fps)), max(1, round(fo * fps))
        lines, last = [], None
        for i in range(c['nf']):       # smoothstep alpha per frame (only the changes are sent)
            a = smoothstep((i + 1) / (nfi + 1)) * smoothstep((c['nf'] - i) / (nfo + 1))
            if last is None or abs(a - last) > 1e-4:
                lines.append(f'{num((c["f0"] + i) / fps - 0.002, 5)} [enter] colorchannelmixer@c{k} aa {a:.4f};')
                last = a
        cmd = work.dir / 'cmd' / f'c{k:02d}.txt'
        cmd.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        extra += ['-loop', '1', '-framerate', str(fps), '-t', num(c['nf'] / fps), '-i', work.rel(info['png'])]
        t0, t1 = c['start'], c['end']
        u = f'clip((t-{num(t0)})/{num(fi)},0,1)'
        y = f"{info['y']}+{num(round(info['rise'], 2))}*(1-(3*{u}*{u}-2*{u}*{u}*{u}))"
        g.append(f"[{idx}:v]format=rgba,setpts=PTS-STARTPTS+{num(t0)}/TB,sendcmd=f='cmd/{cmd.name}',"
                 f"colorchannelmixer@c{k}=aa=0,{TO_YUV},format=yuva444p[c{k}]")
        g.append(f"[{cur}][c{k}]overlay=x={info['x']}:y='{y}':eval=frame:eof_action=pass:format=yuv444:"
                 f"enable='between(t,{num(t0 - 0.001)},{num(t1)})'[o{k}]")
        cur, idx = f'o{k}', idx + 1
    for k, gr in enumerate(plan.get('callouts', [])):     # callout groups: RGBA PNG sequences with baked alpha
        info = gr.get('ty')
        if not info:
            continue
        t0, t1 = gr['start'], gr['end']
        extra += ['-framerate', str(fps), '-start_number', '0', '-i', f'co/g{k:02d}/f_%04d.png']
        g.append(f"[{idx}:v]format=rgba,setpts=PTS-STARTPTS+{num(t0)}/TB,{TO_YUV},format=yuva444p[co{k}]")
        g.append(f"[{cur}][co{k}]overlay=x={info['x']}:y={info['y']}:eof_action=pass:format=yuv444:"
                 f"enable='between(t,{num(t0 - 0.001)},{num(t1)})'[q{k}]")
        cur, idx = f'q{k}', idx + 1
    if plan.get('label_info'):
        L = plan['label_info']
        extra += ['-i', work.rel(L['png'])]
        g.append(f'[{idx}:v]format=rgba,{TO_YUV},format=yuva444p[lab]')
        g.append(f"[{cur}][lab]overlay=x={L['x']}:y={L['y']}:format=yuv444:eof_action=repeat[lbl]")
        cur, idx = 'lbl', idx + 1
    g.append(f'[{cur}]format=yuv420p,{SETPARAMS}[out]')
    return ';\n'.join(g), extra


# ----------------------------------------------------------------------------------------------- bridge fit

def gray_frames(path, w=192, h=108, t=None):
    """Luma frames of a file at w x h (all frames, or the one frame at t seconds)."""
    import numpy as np
    args = [FF, '-v', 'error'] + (['-ss', num(max(0.0, t))] if t is not None else []) + ['-i', str(path)]
    args += (['-frames:v', '1'] if t is not None else []) + ['-vf', f'scale={w}:{h}:flags=area', '-f', 'rawvideo',
                                                             '-pix_fmt', 'gray', '-']
    raw = subprocess.run(args, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def bridge_check(plan, log):
    """For every approved bridge joined by hard cuts: does its in-frame continue the previous shot's last frame and
    does its out-point land on the next shot's first frame? Compares in source space (the shots' own frames, so it
    works for any output size), scans the bridge source for the best-matching frames (PSNR on 192x108 luma) and
    suggests in/out; also reports whether the move has already stopped before 'out' (a stop-then-go at the join).
    Warns below 30 dB or when the suggested out differs from the EDL by > 2 frames."""
    import numpy as np
    fps, segs, joins = plan['fps'], plan['segs'], plan['joins']
    psnr = lambda a, b: float(10 * np.log10(255 ** 2 / max(1e-6, float(((a - b) ** 2).mean()))))
    footage = lambda s: s['type'] in ('clip', 'bridge') and not s.get('slate') and s.get('src')
    rows = []
    for i, s in enumerate(segs):
        if s['type'] != 'bridge' or not footage(s):
            continue
        fr = gray_frames(s['src'])
        n = len(fr)
        if not n:
            continue
        f_in, f_out = round(s['in'] * fps + 0.01), min(n, round(s['out'] * fps + 0.01))
        row = dict(bridge=s['id'], task=s.get('task'), frames=n, edl_in=s['in'], edl_out=s['out'])
        if i > 0 and joins[i - 1]['overlap'] == 0 and footage(segs[i - 1]):
            pv = segs[i - 1]
            last_t = min(pv['out'], pv['probe']['dur']) - 1.0 / fps          # last shown source frame
            prev = gray_frames(pv['src'], t=last_t)
            if len(prev):
                sc = [psnr(prev[0], f) for f in fr[:min(n, 24)]]
                k = int(np.argmax(sc))
                row['head'] = dict(prev=pv['id'], psnr_at_in=round(sc[min(f_in, len(sc) - 1)], 1),
                                   best_match_frame=k, best_psnr=round(sc[k], 1),
                                   suggested_in=round((k + 1) / fps - 0.001, 3))
                if sc[k] < 30:
                    log.warn(f'bridge {s["id"]}: first frames do not continue {pv["id"]} (best {sc[k]:.1f} dB)')
                elif abs((k + 1) - f_in) > 2:
                    log.warn(f'bridge {s["id"]}: frame {k} repeats {pv["id"]}\'s last frame, EDL in {s["in"]}s - '
                             f'set in to {(k + 1) / fps - 0.001:.3f}')
        if i + 1 < len(segs) and joins[i]['overlap'] == 0 and footage(segs[i + 1]):
            nx = segs[i + 1]
            nxt = gray_frames(nx['src'], t=nx['in'])
            if len(nxt):
                sc = [psnr(nxt[0], f) for f in fr]
                k = int(np.argmax(sc))
                moving = [float(np.abs(fr[t] - fr[t - 1]).mean()) for t in range(max(1, f_out - 12), min(n, f_out))]
                row['tail'] = dict(next=nx['id'], psnr_at_out=round(sc[min(f_out, n - 1)], 1),
                                   best_match_frame=k, best_psnr=round(sc[k], 1), suggested_out=round(k / fps, 3),
                                   motion_last_0_5s=round(float(np.mean(moving)), 3) if moving else None)
                if sc[k] < 30:
                    log.warn(f'bridge {s["id"]}: no frame lands on {nx["id"]} (best {sc[k]:.1f} dB)')
                elif abs(k - f_out) > 2:
                    log.warn(f'bridge {s["id"]}: best landing on {nx["id"]} is frame {k} ({k / fps:.2f}s), '
                             f'EDL out {s["out"]}s - set out to {k / fps:.3f}')
                if moving and np.mean(moving) < 0.15:
                    log.warn(f'bridge {s["id"]}: the move has stopped in the 0.5 s before out (stop-then-go into '
                             f'{nx["id"]}); consider an earlier out')
        rows.append(row)
        print('BRIDGE', json.dumps(row))
    return rows


# ----------------------------------------------------------------------------------------------- report

def timeline(plan):
    fps = plan['fps']
    sec = lambda f: round(f / fps, 3)
    items = []
    for i, s in enumerate(plan['segs']):
        d = dict(type=s['type'], id=s['id'], start=sec(s['start']), end=sec(s['end']), frames=s['frames'])
        if s['type'] != 'card':
            d.update(src=short(s['src']) if s.get('src') else None, **{'in': s['in'], 'out': s['out']},
                     speed=s['speed'])
            if s.get('fit', 'cover') != 'cover':
                d['fit'] = s['fit']
            if s['slate']:
                d['slate'] = True
            if s.get('upscaled'):
                d['upscaled'] = f"{s['probe']['w']}x{s['probe']['h']} -> {plan['W']}x{plan['H']}"
        else:
            d.update(style=s['style'], mark=s.get('mark', ''), zh=s['zh'], en=s['en'])
        items.append(d)
        if i < len(plan['joins']):
            j = plan['joins'][i]
            t = dict(type='transition', kind=j['kind'], start=sec(j['start']), end=sec(j['end']),
                     overlap_frames=j['overlap'], dur=j['dur'], between=[j['a'], j['b']])
            if j.get('bridge'):
                t['fallback_for_bridge'] = j['bridge']
            if j.get('fx'):
                t['effect_window'] = [round(j['fx'][0], 3), round(j['fx'][1], 3)]
            items.append(t)
    caps = []
    for c in plan['captions']:
        d = dict(id=c['id'], anchor=c['anchor'], start=round(c['start'], 3), end=round(c['end'], 3), zh=c['zh'],
                 en=c['en'], pos=c['pos'], ink=c['ink'], origin=c['origin'])
        for k in ('style', 'preset', 'role'):
            if c.get(k):
                d[k] = c[k]
        if not c.get('reading', True):
            d['reading'] = False
        if c.get('trimmed'):
            d['end_trimmed_s'] = c['trimmed']
        if c.get('ty'):
            d.update(scrim=c['ty']['scrim'], luma=c['ty']['luma'], box=c['ty']['block'])
            if c['ty'].get('fit_scale'):
                d['fit_scale'] = c['ty']['fit_scale']
        caps.append(d)
    edge = lambda tr: tr and dict(kind=tr['kind'], dur=tr['dur'])
    cos = []
    for gr in plan.get('callouts', []):
        d = dict(clip=gr['id'], groups=gr['group_ids'], start=round(gr['start'], 3), end=round(gr['end'], 3),
                 full_state_s=gr['full_state_s'],
                 items=[dict(id=i['id'], start=round(gr['start'] + i['t0'], 3), clock=i['clock'], zh=i['zh'],
                             fn=i['fn'], en=i['en']) for i in gr['items']])
        if gr.get('ty'):
            geo = {x['id']: x for x in gr['ty']['items']}
            for i in d['items']:
                i.update({k: v for k, v in geo.get(i['id'], {}).items() if k not in ('id', 't0')})
            d['overlay'] = dict(x=gr['ty']['x'], y=gr['ty']['y'], w=gr['ty']['w'], h=gr['ty']['h'], frames=gr['ty']['n'])
            d['layout_warnings'] = gr['ty']['warnings']
        cos.append(d)
    return dict(fps=fps, size=[plan['W'], plan['H']], frames=plan['total'], duration=sec(plan['total']),
                label=plan['label'], fade_in=edge(plan['lead']), fade_out=edge(plan['tail']), items=items,
                captions=caps, callouts=cos, bridge_fallbacks=plan['fallbacks'],
                bridge_check=plan.get('bridge_check', []), safe_box=plan.get('safe_box'))


def print_timeline(tl):
    print(f"\nTIMELINE  {tl['size'][0]}x{tl['size'][1]} @ {tl['fps']} fps  {tl['frames']} frames = {tl['duration']} s")
    if tl.get('fade_in'):
        print(f"    0.000 {tl['fade_in']['dur']:7.3f}    ~ fade in from {tl['fade_in']['kind']}")
    for it in tl['items']:
        if it['type'] == 'transition':
            extra = f"  (bridge {it['fallback_for_bridge']} fallback)" if it.get('fallback_for_bridge') else ''
            fx = f"  fx {it['effect_window'][0]:.3f}-{it['effect_window'][1]:.3f}" if it.get('effect_window') else ''
            print(f"  {it['start']:7.3f} {it['end']:7.3f}    ~ {it['kind']:<9} {it['overlap_frames']:>3} f{fx}{extra}")
        else:
            src = f"{it.get('in', 0):.2f}-{it.get('out', 0):.2f}" if it['type'] != 'card' else it['style']
            sp = f" x{it['speed']}" if it.get('speed', 1) != 1 else ''
            fit = f" [{it['fit']}]" if it.get('fit') else ''
            print(f"  {it['start']:7.3f} {it['end']:7.3f}  {it['type']:<6} {it['id']:<10} {src}{sp}{fit}"
                  f"{'  SLATE' if it.get('slate') else ''}")
    if tl.get('fade_out'):
        d = tl['fade_out']['dur']
        print(f"  {tl['duration'] - d:7.3f} {tl['duration']:7.3f}    ~ fade out ({tl['fade_out']['kind']})")
    for c in tl['captions']:
        sc = f"  scrim(luma {c['luma']})" if c.get('scrim') else ''
        pos = c['pos'] if isinstance(c['pos'], str) else json.dumps(c['pos'])
        print(f"  {c['start']:7.3f} {c['end']:7.3f}  caption {c['id']}@{c['anchor']:<6} {pos:<12} "
              f"{c['zh']} / {c['en']}{sc}")
    for g in tl.get('callouts', []):
        print(f"  {g['start']:7.3f} {g['end']:7.3f}  callouts on {g['clip']} ({len(g['items'])} items, full state "
              f"{g['full_state_s']} s)")
        for i in g['items']:
            print(f"  {i['start']:7.3f}           - {i['id']:<10} {i['zh']} / {i['fn']}")
    for f in tl['bridge_fallbacks']:
        print(f"  bridge {f['bridge']}: {f['reason']} -> {f['used']['kind']} {f['used']['dur']} s")


def count_frames(path):
    r = subprocess.run([FF, '-hide_banner', '-i', str(path), '-map', '0:v', '-c', 'copy', '-f', 'null', '-'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    m = re.findall(r'frame=\s*(\d+)', r.stderr)
    return int(m[-1]) if m else None


# ----------------------------------------------------------------------------------------------- main

def edl_aspect(edl):
    """(w, h) ratio of the EDL's frame: "size" [W, H], else "aspect" ("9:16" or [9, 16]), else 16:9."""
    if edl.get('size'):
        w, h = edl['size']
    elif edl.get('aspect'):
        asp = edl['aspect']
        w, h = re.split(r'[:x/]', asp) if isinstance(asp, str) else asp
    else:
        return 16.0, 9.0
    w, h = float(w), float(h)
    if w <= 0 or h <= 0:
        raise SystemExit(f'EDL size / aspect must be positive, got {edl.get("size") or edl.get("aspect")!r}')
    return w, h


def parse_size(a, edl=None):
    """Output frame and how it was chosen: --size WxH; else --res N = short side N in the EDL's aspect (EDL "size"
    or "aspect", default 16:9; 480 -> 854x480 or 480x854); else the EDL "size"; else 854x480."""
    edl = edl or {}
    if a.size:
        m = re.fullmatch(r'\s*(\d+)\s*[xX*]\s*(\d+)\s*', a.size)
        if not m:
            raise SystemExit(f'--size must look like 1080x1920, got {a.size!r}')
        W, H = int(m.group(1)), int(m.group(2))
        how = '--size'
    elif a.res is None and edl.get('size'):
        W, H = (int(v) for v in edl['size'])
        how = 'EDL size'
    else:
        n = a.res or 480
        w, h = edl_aspect(edl)
        long_side = round(n * max(w, h) / min(w, h) / 2) * 2
        W, H = (long_side, n) if w > h else (n, long_side) if h > w else (n, n)
        how = (f'--res {n}' if a.res else 'default 480') + \
            (f' in the EDL aspect {w:g}:{h:g}' if edl.get('size') or edl.get('aspect') else '')
    if W % 2 or H % 2 or min(W, H) < 64:
        raise SystemExit(f'output size {W}x{H}: both sides must be even and at least 64 px (H.264 4:2:0)')
    return W, H, how


def main(argv=None):
    global PROJECT
    ap = argparse.ArgumentParser(description='EDL-driven editor (see the module docstring or README.md)')
    ap.add_argument('--project', default='.', help='project root; every relative path is resolved against it')
    ap.add_argument('--edl', default='edl.json')
    ap.add_argument('--captions', default=None, help='captions json (default: EDL "captions_file")')
    ap.add_argument('--callouts', default=None, help='callouts json (default: EDL "callouts_file")')
    ap.add_argument('--bridges', default=None, help='approved bridges json (default: EDL "approved_bridges_file")')
    ap.add_argument('--drafts', default=None, help='approved drafts json (default: EDL "approved_drafts_file")')
    ap.add_argument('--res', type=int, default=None, choices=sorted(RES),
                    help='short side of the frame, in the EDL "size" / "aspect" (default 16:9): 480 = 854x480 or '
                         '480x854. Without --res and --size: the EDL "size", else 854x480')
    ap.add_argument('--size', default=None, help='any frame size WxH, e.g. 1080x1920 (overrides --res and the EDL)')
    ap.add_argument('--out', default=None, help='default renders/<edl name>_<W>x<H>.mp4')
    ap.add_argument('--dry', action='store_true', help='plan and print the timeline only')
    ap.add_argument('--fresh', action='store_true', help='re-render cached segments')
    ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--crf', type=int, default=None, help='final H.264 crf (default 16 up to 480 px, 18 above)')
    ap.add_argument('--offset-basis', choices=['trimmed', 'source'], default=None,
                    help='override how caption offsets are read (default: per file/caption, else trimmed)')
    a = ap.parse_args(argv)
    TY.utf8_stdout()

    PROJECT = Path(a.project).resolve()
    if not PROJECT.is_dir():
        raise SystemExit(f'project folder not found: {PROJECT}')
    log = Log()
    edl_path = rel_root(a.edl)
    edl = load_json(edl_path)
    if edl is None:
        raise SystemExit(f'EDL not found: {edl_path}')
    W, H, size_from = parse_size(a, edl)
    orient = TY.orientation(W, H)
    edl = TY.orient_tree(edl, orient)

    def doc(cli, key, what, required=True):
        named = cli or edl.get(key)
        if not named:
            return None, None
        p = rel_root(named)
        d = load_json(p)
        if d is None:
            if required:
                raise SystemExit(f'{what} file not found: {p}')
            log.warn(f'{what} file not found: {short(p)} (treated as empty)')
            return p, {}
        return p, TY.orient_tree(d, orient)

    cap_path, cap_doc = doc(a.captions, 'captions_file', 'captions')
    co_path, co_doc = doc(a.callouts, 'callouts_file', 'callouts')
    dr_path, drafts = doc(a.drafts, 'approved_drafts_file', 'approved drafts', required=False)
    br_path, bridges = doc(a.bridges, 'approved_bridges_file', 'approved bridges', required=False)
    print(f'PROJECT {PROJECT}\nEDL {short(edl_path)} | captions {short(cap_path) if cap_path else "(none)"} | '
          f'callouts {short(co_path) if co_path else "(none)"} | drafts {short(dr_path) if dr_path else "(none)"} | '
          f'bridges {short(br_path) if br_path else "(none)"} | {W}x{H} ({orient}, {size_from})')
    style = TY.Style(cap_doc.get('style') if isinstance(cap_doc, dict) else None, W, H, PROJECT)
    if isinstance(co_doc, dict) and co_doc.get('_style'):
        style.d['callout'] = TY.deep_merge(style.d['callout'], co_doc['_style'])
    ledgers = dict(drafts={k: v for k, v in (drafts or {}).items() if not str(k).startswith('_')},
                   bridges={k: v for k, v in (bridges or {}).items() if not str(k).startswith('_')},
                   bridges_name=br_path.name if br_path else None)
    plan = build_plan(edl, cap_doc, W, H, style, log, a.offset_basis, ledgers, co_doc, size_from)
    plan['safe_box'] = safe_box_check(plan, style, log)
    out = rel_root(a.out or f'renders/{edl_path.stem}_{W}x{H}.mp4')
    tl = timeline(plan)
    meta = dict(edl=short(edl_path), captions_file=short(cap_path) if cap_path else None,
                callouts_file=short(co_path) if co_path else None, out=short(out))
    tl.update(meta)
    if a.dry:
        print_timeline(tl)
        print(f'\nDRY run: nothing rendered ({len(log.warnings)} warning(s))')
        tl['warnings'] = log.warnings
        return tl

    cache = PROJECT / '.cache' / 'edl_editor' / f'{W}x{H}'
    cache.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for sid, how in ex.map(lambda s: render_segment(s, plan, style, cache, a.fresh), plan['segs']):
            print(f'SEG {sid:<12} {how}', flush=True)

    plan['bridge_check'] = bridge_check(plan, log)
    tag = hashlib.sha1(str(out).encode('utf-8')).hexdigest()[:8]
    stem = out.stem.encode('ascii', 'ignore').decode() or 'out'
    work = Work(cache / 'final_work' / f'{safe_id(stem)}_{tag}')
    typeset(plan, style, work, log)
    tl = timeline(plan)
    tl.update(meta)
    print_timeline(tl)
    graph, extra = final_graph(plan, work)
    gfile = work.dir / 'final_graph.txt'
    gfile.write_text(graph, encoding='utf-8')
    out.parent.mkdir(parents=True, exist_ok=True)
    crf = a.crf if a.crf is not None else (16 if min(W, H) <= 480 else 18)
    inputs = [x for s in plan['segs'] for x in ('-i', s['file'])] + extra
    tmp = work.dir / 'final.part.mp4'
    print(f'\nRENDER {len(plan["segs"])} segments, {len(plan["joins"])} joins, {len(plan["captions"])} captions, '
          f'{len(plan["callouts"])} callout group(s) -> {short(out)}', flush=True)
    ffmpeg([*inputs, graph_opt(), gfile.name, '-map', '[out]', '-frames:v', str(plan['total']),
            '-r', str(plan['fps']), '-an', '-c:v', 'libx264', '-preset', 'slow', '-crf', str(crf),
            '-profile:v', 'high', '-pix_fmt', 'yuv420p', *COLOR_TAGS, '-movflags', '+faststart', tmp.name],
           work.dir, quiet=False)
    shutil.move(str(tmp), str(out))
    n = count_frames(out)
    tl['verified_frames'] = n
    tl['warnings'] = log.warnings
    tl_path = out.with_name(out.stem + '.timeline.json')
    tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding='utf-8')
    ok = 'OK' if n == plan['total'] else f'MISMATCH (expected {plan["total"]})'
    print(f'\nOUT {out}\n  frames {n} {ok}; timeline {tl_path}')
    if log.warnings:
        print(f'  {len(log.warnings)} warning(s) - listed above and in the timeline json')
    return tl


if __name__ == '__main__':
    main()
