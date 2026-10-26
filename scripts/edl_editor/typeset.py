# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.1",
#     "numpy>=1.24",
#     "imageio-ffmpeg>=0.5.1",
# ]
# ///
"""Typography for edit.py: caption blocks, title / chapter / end cards, the corner label, missing-source slates and
leader-line callouts, drawn with Pillow (supersampled, letter-spaced, soft shadow, optional feathered scrim) into
PNGs that ffmpeg overlays and fades.

Run on its own to see which fonts this machine resolves (and optionally a specimen still):
    uv run typeset.py [--project DIR] [--size 1920x1080] [--specimen out.png]

Style
  The style lives in captions.json "style" (see README.md). Every key is optional: DEFAULT_STYLE below is merged
  under it. All px values are for ref_resolution [1920, 1080] and are scaled by the SHORT side of the output frame
  (s = min(W, H) / min(ref)), so a 1080x1920 vertical frame uses the same type sizes as a 1920x1080 one. Position
  keys x / right / top / bottom are margins in those scaled px; cx / cy are centre coordinates on the ref frame,
  mapped proportionally onto the output frame.
  Orientation: a "portrait" (H > W), "landscape" (W > H) or "square" section anywhere in the style (or in the EDL,
  captions and callouts files) is merged over its parent when the output has that orientation. DEFAULT_STYLE has a
  "portrait" section that moves captions and the corner label clear of the UI that short-video apps draw over the
  bottom and right of a vertical frame.
  Presets: "preset": "latin" (style-wide, or per caption / card) swaps the type to a Latin-script setting (main
  line in a sans serif at 44 px, no CJK punctuation handling). "feed_safe" (style-wide) moves every vertical
  position, the corner label and the card small print inside the conservative feed safe box (x 65-888,
  y 288-1248 on 1080x1920) and sets "safe_box", which edit.py checks. "note" (per caption) sets small print, e.g.
  an AI notice under a product end shot. The style-wide "preset" may be a list (["feed_safe", "latin"]). Custom
  presets go in style "presets": {name: {...}}.
  Safe box: style "safe_box" [x0, y0, x1, y1] in px of a frame whose short side is 1080 (scaled like every other px
  value; usually inside a "portrait" section). edit.py warns, also with --dry, when a caption block, callout label
  or dot, or card small print leaves it.

Fonts
  A font spec is one of: "@role" (a fallback list below), a file name searched in the font folders, a path
  relative to the project, or an absolute path. "file.ttc#2" picks face 2 of a collection, "file.ttc#Face Name"
  picks the face whose name matches. Search order: $EDL_EDITOR_FONT_DIRS, <project>/fonts, fonts/ next to this
  file, then the system font folders (Windows, macOS, Linux). Each choice is printed once ("FONT ...").
  Roles: zh_light (CJK Light: running text, cards, labels), zh_regular (CJK Regular: drop-cap glyph, key words,
  callout names), en (Latin display face for tracked caps), en_bold, latin_regular / latin_light (preset "latin").

Type hierarchy (style "caption.levels", largest first):
  glyph  the channel label before '｜' (e.g. "静｜..."), accent colour, CJK Regular. With caption.layout "drop"
         (default) it is a drop cap as tall as the main + secondary lines together, with a hairline rule between
         it and the stack; layout "inline" sets it on the main line's baseline instead.
  emph   the key words of the main line (caption "emph"), CJK Regular, a step larger and brighter.
  rest   the rest of the main line, CJK Light, a step smaller and softer.
  en     the secondary line (usually English), Latin display face in caps, clearly smaller, wide tracking;
         "en_emph" substrings (product names) are set bold at the same size.
Caption "emph": a list of substrings of the main line (after the glyph); a string = level "emph", a dict
{"text", "level"} picks another level (an overview's {"text": "轻、静、快", "level": "glyph"} sets those three
characters glyph-size in the accent colour). Every non-overlapping occurrence is marked; earlier entries win.
Punctuation inside a larger run is set at caption.punct_level (rest) in the run's ink, in a narrow slot (its ink
width + punct_pad_l_em before / punct_pad_r_em after, of the run size), so a run like 轻、静、快 keeps an even
rhythm; other pause marks (caption.punct_trail chars ，、。…) get a compressed half-width slot (CJK punctuation
squeezing: ink + pad_l_em / pad_r_em of its own size, nothing after the last mark of a line) instead of a
full-em box whose empty half reads as a hole after a large key word. Mixed sizes share one baseline. Chapter
captions ("style": "chapter") keep their own single-level style.
English fit (caption.en_fit): a secondary line wider than the main line has its tracking tightened, never below
min_tracking_em, so the two lines lock up as one block. Wrapping: caption "zh_break" / "en_break" (a substring or
a list) wraps the main line (after the glyph) / the secondary line after that substring; the drop-cap glyph then
spans the whole stack. A block wider than the frame minus the safe margins is scaled down to fit (NOTE printed);
every block is kept inside the safe margin.
Sizes at the reference 1080 px short side: glyph 104, emph 64, rest 48, en 20; chapter 96 / 21; cards: mark 124,
title 46, end 50, en 20; callouts: name 36, function line 22, en 13.5.
Light captions get the feathered scrim when the frame under them is brighter than scrim_params.threshold (0.24).
"""

import argparse
import math
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

LANCZOS = Image.Resampling.LANCZOS
BILINEAR = Image.Resampling.BILINEAR
AFFINE = Image.Transform.AFFINE
TYPE_VERSION = 'type/1'          # part of the card segment cache key in edit.py: bump when card drawing changes
ORIENTS = ('portrait', 'landscape', 'square')

# full-width and Latin punctuation: inside a larger run it is set at caption.punct_level
PUNCT = set('，。、；：！？·・…—–「」『』（）《》〈〉,.;:!?')

# ----------------------------------------------------------------------------------------------- fonts

# Fallback lists per role: Windows first (the look the defaults were tuned on), then macOS, then Linux.
# "file#Face Name" picks a face inside a .ttc collection (Noto CJK collections start with the Japanese face).
FONT_ROLES = {
    'zh_light': ['msyhl.ttc', 'Dengl.ttf',                                          # Windows: YaHei Light, DengXian Light
                 'PingFang.ttc#PingFang SC Light', 'Hiragino Sans GB.ttc#Hiragino Sans GB W3',
                 'STHeiti Light.ttc',                                                # macOS
                 'NotoSansCJK-Light.ttc#Noto Sans CJK SC', 'NotoSansCJKsc-Light.otf', 'NotoSansSC-Light.otf',
                 'NotoSansCJK-DemiLight.ttc#Noto Sans CJK SC', 'NotoSansCJK-Regular.ttc#Noto Sans CJK SC',
                 'NotoSansCJKsc-Regular.otf', 'NotoSansSC-Regular.otf', 'SourceHanSansSC-Light.otf',
                 'SourceHanSansSC-Regular.otf', 'wqy-microhei.ttc', 'wqy-zenhei.ttc',  # Linux
                 'msyh.ttc', 'simhei.ttf', 'Arial Unicode.ttf', 'DroidSansFallbackFull.ttf'],
    'zh_regular': ['msyh.ttc', 'Deng.ttf', 'simhei.ttf',
                   'PingFang.ttc#PingFang SC Regular', 'Hiragino Sans GB.ttc#Hiragino Sans GB W6',
                   'STHeiti Medium.ttc',
                   'NotoSansCJK-Regular.ttc#Noto Sans CJK SC', 'NotoSansCJKsc-Regular.otf', 'NotoSansSC-Regular.otf',
                   'NotoSansCJK-Medium.ttc#Noto Sans CJK SC', 'SourceHanSansSC-Regular.otf',
                   'wqy-microhei.ttc', 'wqy-zenhei.ttc', '@zh_light'],
    'en': ['GOTHIC.TTF', 'segoeuil.ttf', 'segoeui.ttf',                              # Century Gothic, Segoe UI Light
           'Avenir Next.ttc#Avenir Next Regular', 'Avenir.ttc#Avenir Book', 'HelveticaNeue.ttc#Helvetica Neue Light',
           'Helvetica.ttc', 'NotoSans-Light.ttf', 'NotoSans-Regular.ttf', 'DejaVuSans.ttf', '@zh_light'],
    'en_bold': ['GOTHICB.TTF', 'segoeuib.ttf', 'Avenir Next.ttc#Avenir Next Demi Bold', 'Avenir.ttc#Avenir Heavy',
                'HelveticaNeue.ttc#Helvetica Neue Bold', 'NotoSans-Bold.ttf', 'DejaVuSans-Bold.ttf', '@zh_regular'],
    'latin_regular': ['segoeui.ttf', 'HelveticaNeue.ttc#Helvetica Neue Regular', 'Avenir Next.ttc#Avenir Next Regular',
                      'NotoSans-Regular.ttf', 'DejaVuSans.ttf', '@zh_regular'],
    'latin_light': ['segoeuil.ttf', 'HelveticaNeue.ttc#Helvetica Neue Light', 'Avenir Next.ttc#Avenir Next Regular',
                    'NotoSans-Light.ttf', 'NotoSans-Regular.ttf', 'DejaVuSans.ttf', '@latin_regular'],
}
FONT_EXT = ('.ttf', '.ttc', '.otf', '.otc')
_INDEX = {}
_FACES = {}
_REPORTED = set()


def font_dirs(project=None):
    """Folders searched for a font file name, in order."""
    dirs = []
    env = os.environ.get('EDL_EDITOR_FONT_DIRS')
    if env:
        dirs += [Path(p) for p in env.split(os.pathsep) if p]
    if project:
        dirs.append(Path(project) / 'fonts')
    dirs.append(Path(__file__).resolve().parent / 'fonts')
    if sys.platform == 'win32':
        dirs.append(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts')
        if os.environ.get('LOCALAPPDATA'):
            dirs.append(Path(os.environ['LOCALAPPDATA']) / 'Microsoft' / 'Windows' / 'Fonts')
    elif sys.platform == 'darwin':
        dirs += [Path('/System/Library/Fonts'), Path('/System/Library/Fonts/Supplemental'), Path('/Library/Fonts'),
                 Path.home() / 'Library' / 'Fonts']
        try:          # PingFang and other downloadable system fonts live in the font asset store on macOS 13+
            dirs += sorted(Path('/System/Library/AssetsV2').glob('com_apple_MobileAsset_Font*/*/AssetData'))
        except OSError:
            pass
    else:
        xdg = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
        dirs += [Path(p) / 'fonts' for p in xdg.split(':') if p]
        dirs += [Path.home() / '.local' / 'share' / 'fonts', Path.home() / '.fonts']
    return dirs


def _font_index(project):
    """lower-case file name -> first path found (folders searched recursively, in font_dirs order)."""
    key = str(project)
    if key not in _INDEX:
        idx = {}
        for d in font_dirs(project):
            if not d.is_dir():
                continue
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(FONT_EXT):
                        idx.setdefault(f.lower(), Path(root) / f)
        _INDEX[key] = idx
    return _INDEX[key]


def _pick_face(path, hint):
    """Face index in a font collection: hint = a number, or a face name ("Family Style" or "Family")."""
    if hint.isdigit():
        return int(hint)
    key = (str(path), hint.lower())
    if key not in _FACES:
        h, fam_hit, prefix_hit, found = hint.lower(), None, None, None
        for i in range(64):
            try:
                fam, sty = ImageFont.truetype(str(path), 12, index=i).getname()
            except OSError:
                break
            fam, sty = (fam or '').lower(), (sty or '').lower()
            if h in (f'{fam} {sty}', fam if sty == 'regular' else None):
                found = i
                break
            if fam == h and fam_hit is None:
                fam_hit = i
            if fam.startswith(h) and prefix_hit is None:      # legacy family names such as "... SC Light"
                prefix_hit = i
        _FACES[key] = next((v for v in (found, fam_hit, prefix_hit) if v is not None), None)
    return _FACES[key]


def resolve_font(spec, project=None, _seen=()):
    """Font spec -> 'path' or 'path#face_index', or None when nothing matches on this machine."""
    if not spec:
        return None
    spec = str(spec)
    if spec.startswith('@'):
        role = spec[1:]
        if role in _seen or role not in FONT_ROLES:
            return None
        for cand in FONT_ROLES[role]:
            r = resolve_font(cand, project, _seen + (role,))
            if r:
                return r
        return None
    name, hint = spec, ''
    head, sep, tail = spec.rpartition('#')
    if sep and head and not any(c in tail for c in '/\\'):
        name, hint = head, tail
    p = Path(name)
    if p.is_absolute() or len(p.parts) > 1:
        p = p if p.is_absolute() else Path(project or '.') / p
        p = p if p.is_file() else None
    else:
        p = _font_index(project).get(name.lower())
    if p is None:
        return None
    if not hint:
        return str(p)
    i = _pick_face(p, hint)
    return None if i is None else f'{p}#{i}'


def split_font(resolved):
    """'path#3' -> ('path', 3); 'path' -> ('path', 0)."""
    head, sep, tail = str(resolved).rpartition('#')
    return (head, int(tail)) if sep and tail.isdigit() else (str(resolved), 0)


def describe_font(resolved):
    path, i = split_font(resolved)
    try:
        fam, sty = ImageFont.truetype(path, 12, index=i).getname()
        return f'{path}' + (f'#{i}' if i else '') + f' ({fam} {sty})'
    except OSError:
        return path


# ----------------------------------------------------------------------------------------------- style

DEFAULT_STYLE = {
    'ref_resolution': [1920, 1080],
    'font_zh': '@zh_light', 'font_zh_fallback': None,
    'font_glyph': '@zh_regular',
    'font_en': '@en', 'font_en_fallback': None,
    'safe_px': 48,                      # blocks are kept this far inside the frame edge (and scaled down to fit)
    'caption': {
        'layout': 'drop',               # channel glyph as a drop cap beside the main + secondary stack ('inline')
        'levels': {                     # px at the reference resolution; ink = key of the ink palette
            'glyph': {'font': '@zh_regular', 'px': 104, 'tracking_em': 0.0, 'ink': 'accent'},
            'emph': {'font': '@zh_regular', 'px': 64, 'tracking_em': 0.06, 'ink': 'zh_hi'},
            'rest': {'font': '@zh_light', 'px': 48, 'tracking_em': 0.10, 'ink': 'zh'},
            'en': {'font': '@en', 'px': 20, 'tracking_em': 0.28, 'ink': 'en'},
            'en_emph': {'font': '@en_bold', 'px': 20, 'tracking_em': 0.28, 'ink': 'en_hi'},
        },
        # punctuation inside a larger run: punct_level size, run ink, narrow slot (pad_l / pad_r em of the run size)
        'punct_level': 'rest', 'punct_pad_em': 0.2, 'punct_pad_l_em': 0.13, 'punct_pad_r_em': 0.27, 'punct_ink': 'run',
        # every other pause mark: compressed half-width slot, ink + pad_l_em before + pad_r_em after (own size)
        'punct_trail': {'on': True, 'chars': '，、。；：！？', 'pad_l_em': 0.06, 'pad_r_em': 0.42},
        'en_uppercase': True,
        'line_gap_px': 22,              # main line ink bottom -> secondary line cap top
        'zh_leading_px': 16, 'en_leading_px': 10,   # between wrapped lines (caption zh_break / en_break)
        'en_fit': {'on': True, 'min_tracking_em': 0.18},  # secondary tracking tightens to the main line's measure
        'hairline': {'w_px': 2, 'opacity': 0.55, 'space_px': 26, 'span': 1.0,  # drop: rule spans the stack
                     'h_em': 0.78},     # inline: rule height in em of the rest size
    },
    'chapter': {'zh_px': 96, 'zh_tracking_em': 0.5, 'en_px': 21, 'en_tracking_em': 0.5, 'line_gap_px': 26,
                'fade_in': 0.6, 'fade_out': 0.6},
    'positions': {'lower_left': {'x': 128, 'bottom': 108}, 'upper_left': {'x': 128, 'top': 104},
                  'lower_center': {'cx': 960, 'bottom': 108}, 'upper_center': {'cx': 960, 'top': 104},
                  'upper_right': {'right': 128, 'top': 104},
                  'lower_right': {'right': 128, 'bottom': 156},      # kept clear of the corner label
                  'mid_left': {'x': 128, 'cy': 540}, 'mid_right': {'right': 128, 'cy': 540},
                  'center': {'cx': 960, 'cy': 540}},
    'ink': {'light': {'zh': '#E3DCD0', 'zh_hi': '#F6F1E8', 'en': '#BFB6A8', 'en_hi': '#E3DCD0',
                      'accent': '#CDAE7E',
                      'shadow': {'color': '#000000', 'opacity': 0.5, 'blur_px': 10, 'dy_px': 2}},
            'dark': {'zh': '#38322C', 'zh_hi': '#1A1612', 'en': '#433C35', 'en_hi': '#2A2520',
                     'accent': '#93703D', 'shadow': None}},
    # scrim rule: light ink, mean luma (0-1) of block + pad at the caption's mid-time above threshold
    'scrim_params': {'threshold': 0.24, 'pad_px': 60, 'opacity': 0.30, 'feather_px': 70},
    'fade_in': 0.4, 'fade_out': 0.4, 'rise_px': 6,
    'min_px': 9,                      # legibility floor on small (480p) renders, in output pixels
    'label': {'font': '@zh_light', 'px': 24, 'color': '#ECE6DB', 'opacity': 0.62, 'tracking_em': 0.04,
              'right': 44, 'bottom': 34, 'shadow': 0.7},       # 'top' instead of 'bottom' puts it in the top corner
    'cards': {'bg': '#0E0C0B',
              'mark': {'font': '@en', 'px': 124, 'tracking_em': 0.62, 'color': '#ECE6DB'},
              'zh': {'font': '@zh_light', 'px': 46, 'tracking_em': 0.5, 'color': '#CBC3B6'},
              'en': {'font': '@en', 'px': 20, 'tracking_em': 0.42, 'color': '#8F887E'},
              'note': {'font': '@zh_light', 'px': 22, 'tracking_em': 0.12, 'color': '#7D776E', 'bottom': 72},
              # card main line: channel_chars (e.g. "轻静快") in the caption glyph colour and weight, '·' dim accent
              'channel_chars': '', 'channel': {'font': '@zh_regular', 'color': '#CDAE7E'},
              'sep_color': '#6E5E48',
              'end_zh_px': 50, 'gap_mark_zh_px': 44, 'gap_zh_en_px': 26,
              'rule': {'color': '#C9A97A', 'w_px': 72, 'h_px': 1, 'above_mark_px': 32, 'on': ['end']},
              'no_mark_zh_scale': 1.6,
              'side_px': 96},           # a card line wider than the frame minus 2 x side_px is scaled down
    # leader-line callouts (exploded views, cutaways, part names); overridable from callouts.json "_style".
    # Same hierarchy as the captions: name = key word (CJK Regular), function line = rest (Light), en = tracked caps
    'callout': {
        'name': {'font': '@zh_regular', 'px': 36, 'tracking_em': 0.06},
        'fn': {'font': '@zh_light', 'px': 22, 'tracking_em': 0.08},
        'en': {'font': '@en', 'px': 13.5, 'tracking_em': 0.30, 'uppercase': True},
        'en_fit': {'on': True, 'min_tracking_em': 0.18},   # English tightens to the Chinese lines' width
        'gap_name_fn_px': 12, 'gap_fn_en_px': 9,
        'text_gap_px': 14,              # line end -> text block
        'line_w_px': 1.5, 'line_opacity': 0.9,
        'dot_r_px': 4.2, 'ring_r_px': 10.0, 'ring_w_px': 1.2, 'ring_opacity': 0.55,
        'dot_edge': {'w_px': 1.4, 'opacity': 0.5},       # crisp dark edge so the dot reads on white parts
        'knee_px': 44,                  # elbow route: horizontal shelf length before the label
        'line_shadow': {'opacity': 0.6, 'blur_px': 4, 'dy_px': 1},
        'text_shadow': {'opacity': 0.5, 'blur_px': 9, 'dy_px': 2},
        'timing': {'dot_s': 0.2, 'ring_s': 0.55, 'line_delay_s': 0.06, 'draw_s': 0.3, 'text_delay_s': 0.26,
                   'text_fade_s': 0.4, 'stagger_s': 0.35, 'fade_out_s': 0.5},
        'slide_px': 8,                  # text glides this far outward while it fades in
        'min_px': 7,                    # legibility floor for callout text on small renders (output px)
        'auto_ink_luma': 0.36,          # ink 'auto': dark ink when the background under the labels is brighter
        'group_ink': True,              # ink 'auto' decided once per group (median luma), never mixed in one set
        'scrim': {'threshold': 0.34, 'pad_px': 34, 'opacity': 0.28, 'feather_px': 46},   # light ink only
        'margin_px': 48,                # safe margin to the frame edge (warning only)
        'ink': {'light': {'name': '#F6F1E8', 'fn': '#D2CABD', 'en': '#9E968A', 'line': '#C9A97A',
                          'dot': '#ECE6DB', 'ring': '#C9A97A', 'shadow': '#000000'},
                'dark': {'name': '#1A1612', 'fn': '#463F38', 'en': '#5A5249', 'line': '#8E6B3A',
                         'dot': '#2B2723', 'ring': '#97733F', 'shadow': None}},
    },
    # merged over everything above when the output is vertical (H > W): short-video apps draw their own UI over
    # roughly the top 12 %, the bottom 20 % and a column on the right of the lower half. Looser than the
    # conservative feed safe box: feed ads use the style preset "feed_safe" (PRESETS below)
    'portrait': {
        'positions': {'lower_left': {'x': 72, 'bottom': 420}, 'lower_center': {'cx': 960, 'bottom': 420},
                      'lower_right': {'right': 168, 'bottom': 420}, 'upper_left': {'x': 72, 'top': 260},
                      'upper_center': {'cx': 960, 'top': 260}, 'upper_right': {'right': 72, 'top': 260},
                      'mid_left': {'x': 72, 'cy': 540}, 'mid_right': {'right': 168, 'cy': 540}},
        'label': {'top': 236, 'right': 48},
        'cards': {'note': {'bottom': 400}, 'side_px': 72},
    },
}

# Built-in presets ("preset": name or list of names in the style, a caption or a card). latin = a Latin-script
# main line; feed_safe = vertical positions inside the conservative feed safe box (references/02-formats.md section 5)
# and a safe_box that edit.py checks; note = small print (an AI notice under a product end shot), not reading text.
FEED_SAFE_BOX = [65, 288, 888, 1248]     # x0, y0, x1, y1 on a 1080x1920 frame: top 15 %, bottom 35 %, left 6 %, right 10 %
PRESETS = {
    'feed_safe': {
        'portrait': {
            # upper positions start below the corner label (top right of the box) when one is drawn
            'positions': {'lower_left': {'x': 72, 'bottom': 700}, 'lower_center': {'cx': 960, 'bottom': 700},
                          'lower_right': {'right': 200, 'bottom': 700}, 'upper_left': {'x': 72, 'top': 340},
                          'upper_center': {'cx': 960, 'top': 340}, 'upper_right': {'right': 200, 'top': 340},
                          'mid_left': {'x': 72, 'cy': 540}, 'mid_right': {'right': 200, 'cy': 540},
                          'center': {'cx': 960, 'cy': 540}},
            'label': {'top': 296, 'right': 200},
            'cards': {'note': {'bottom': 700}, 'side_px': 192},
            'safe_box': FEED_SAFE_BOX,
        },
    },
    'note': {
        'caption': {
            'levels': {'glyph': {'font': '@zh_light', 'px': 26, 'tracking_em': 0.08, 'ink': 'en'},
                       'emph': {'font': '@zh_light', 'px': 26, 'tracking_em': 0.08, 'ink': 'en'},
                       'rest': {'font': '@zh_light', 'px': 26, 'tracking_em': 0.08, 'ink': 'en'},
                       'en': {'font': '@latin_light', 'px': 15, 'tracking_em': 0.12},
                       'en_emph': {'font': '@latin_regular', 'px': 15, 'tracking_em': 0.12}},
            'layout': 'inline', 'en_uppercase': False, 'line_gap_px': 10, 'zh_leading_px': 8, 'en_leading_px': 6,
            'en_fit': {'on': False}, 'hairline': {'space_px': 12}},
    },
    'latin': {
        'caption': {
            'levels': {'glyph': {'font': '@en', 'px': 26, 'tracking_em': 0.36, 'ink': 'accent'},
                       'emph': {'font': '@latin_regular', 'px': 44, 'tracking_em': 0.012, 'ink': 'zh_hi'},
                       'rest': {'font': '@latin_light', 'px': 44, 'tracking_em': 0.012, 'ink': 'zh'},
                       'en': {'px': 17, 'tracking_em': 0.34}, 'en_emph': {'px': 17, 'tracking_em': 0.34}},
            'punct_level': 'none', 'punct_trail': {'on': False},
            'line_gap_px': 18, 'zh_leading_px': 10, 'hairline': {'space_px': 22}, 'ref_glyph': 'Hg'},
        'chapter': {'zh_px': 72, 'zh_font': '@en', 'ref_glyph': 'H'},
        'cards': {'zh': {'font': '@latin_light', 'tracking_em': 0.18}, 'zh_ref': 'Hg',
                  'note': {'font': '@latin_light', 'tracking_em': 0.04}},
        'callout': {'ref_glyph': 'Hg', 'name': {'font': '@latin_regular', 'px': 30, 'tracking_em': 0.01},
                    'fn': {'font': '@latin_light', 'px': 21, 'tracking_em': 0.012}, 'gap_name_fn_px': 6},
        'label': {'font': '@latin_light', 'tracking_em': 0.02},
    },
}


def deep_merge(base, over):
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif v is not None or k not in out:
            out[k] = v
    return out


def utf8_stdout():
    """Print CJK text safely when the output is piped (Windows pipes default to a legacy code page)."""
    for s in (sys.stdout, sys.stderr):
        try:
            if not s.isatty() and (s.encoding or '').lower().replace('-', '') != 'utf8':
                s.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def orientation(W, H):
    return 'portrait' if H > W else 'landscape' if W > H else 'square'


def orient_tree(obj, orient):
    """Merge every "portrait" / "landscape" / "square" sub-dict matching orient over its parent (recursively) and
    drop the others, so one EDL / captions / callouts / style file can serve horizontal and vertical cuts."""
    if isinstance(obj, list):
        return [orient_tree(x, orient) for x in obj]
    if not isinstance(obj, dict):
        return obj
    base = {k: orient_tree(v, orient) for k, v in obj.items() if k not in ORIENTS}
    over = obj.get(orient)
    return deep_merge(base, orient_tree(over, orient)) if isinstance(over, dict) else base


def _preset(name, raw):
    if name not in PRESETS and name not in (raw.get('presets') or {}):
        print(f'NOTE style preset {name!r} is not defined (built in: {sorted(PRESETS)}); ignored')
    return deep_merge(PRESETS.get(name, {}), (raw.get('presets') or {}).get(name, {}))


def compose_style(raw, orient, preset=None):
    """DEFAULT_STYLE < style-wide preset(s) < raw style < per-item preset; orientation sections applied to each.
    The style-wide "preset" is a name or a list of names, merged in order (e.g. ["feed_safe", "latin"])."""
    raw = orient_tree(raw or {}, orient)
    films = raw.get('preset') or []
    films = [films] if isinstance(films, str) else list(films)
    base = deepcopy(DEFAULT_STYLE)
    for film in films:
        base = deep_merge(base, _preset(film, raw))
    d = deep_merge(orient_tree(base, orient), {k: v for k, v in raw.items() if k not in ('preset', 'presets')})
    if preset and preset not in films:
        d = deep_merge(d, orient_tree(_preset(preset, raw), orient))
    return d


def rgb(hexstr):
    h = hexstr.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def run_width(text, font, track):
    if not text:
        return 0.0
    return sum(font.getlength(ch) for ch in text) + track * (len(text) - 1)


def draw_run(layers, x, base, text, font, track, fill):
    """Draw text char by char (letter-spacing) on every (ImageDraw, fill) layer; fill None = use `fill`."""
    for ch in text:
        for d, f in layers:
            d.text((x, base), ch, font=font, fill=fill if f is None else f, anchor='ls')
        x += font.getlength(ch) + track
    return x - track


class Style:
    """Merged style for one output size. s = output short side / reference short side."""

    def __init__(self, raw, W, H, project=None, preset=None):
        self.raw, self.project, self.preset = raw or {}, project, preset
        self.orient = orientation(W, H)
        self.d = compose_style(self.raw, self.orient, preset)
        ref = self.d.get('ref_resolution') or [1920, 1080]
        self.refW, self.refH = float(ref[0]), float(ref[1])
        self.W, self.H = W, H
        self.s = min(W, H) / min(self.refW, self.refH)
        self.ss = 3 if min(W, H) <= 720 else 2
        self._fonts, self._variants = {}, {}
        d = self.d
        self.font_zh = self.path(d.get('font_zh'), d.get('font_zh_fallback'), '@zh_light')
        self.font_glyph = self.path(d.get('font_glyph'), '@zh_regular', self.font_zh)
        self.font_en = self.path(d.get('font_en'), d.get('font_en_fallback'), '@en', self.font_zh)

    def variant(self, preset):
        """The same style with a per-item preset (caption / card "preset") merged on top; None = self."""
        if not preset or preset == self.preset:
            return self
        if preset not in self._variants:
            self._variants[preset] = Style(self.raw, self.W, self.H, self.project, preset)
        return self._variants[preset]

    def path(self, *names):
        """First font spec of names that exists on this machine (resolved 'path' or 'path#index')."""
        tried = []
        for s in names:
            if not s:
                continue
            r = resolve_font(s, self.project)
            if r:
                key = (str(s), r, tuple(map(str, tried)))
                if key not in _REPORTED and (str(s) != r or tried):     # an already resolved default: silent
                    _REPORTED.add(key)
                    miss = f'  [not found here: {", ".join(map(str, tried))}]' if tried else ''
                    print(f'FONT {s} -> {describe_font(r)}{miss}', flush=True)
                return r
            tried.append(s)
        raise SystemExit(f'font not found: {tried}. Install a CJK font (e.g. Noto Sans CJK SC), or put a .ttf/.otf '
                         f'into <project>/fonts and name it in the style (font_zh / font_en / levels.*.font).')

    def font(self, spec, px):
        path, idx = split_font(spec)
        key = (path, idx, round(px * 4) / 4)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(path, key[2], index=idx)
        return self._fonts[key]

    def font_like(self, f, px):
        """Same face as the FreeTypeFont f at another size."""
        return self.font(f'{f.path}#{f.index}', px)

    def fpx(self, px_ref):
        """Font size in output px (scaled, with the legibility floor)."""
        return max(float(self.d.get('min_px', 0)), float(px_ref) * self.s)

    @contextmanager
    def scaled(self, k):
        """Temporarily scale every px value (caption fit)."""
        s0 = self.s
        self.s = s0 * k
        try:
            yield self
        finally:
            self.s = s0


def _canvas(wt, ht, ss):
    img = Image.new('RGBA', (wt * ss, ht * ss), (0, 0, 0, 0))
    mask = Image.new('L', (wt * ss, ht * ss), 0)
    return img, mask


def _soft(mask, sigma, opacity, color, offset=(0, 0)):
    m = mask
    if offset != (0, 0):
        m = Image.new('L', mask.size, 0)
        m.paste(mask, (round(offset[0]), round(offset[1])))
    if sigma > 0.3:
        m = m.filter(ImageFilter.GaussianBlur(sigma))
    m = m.point(lambda v: round(v * opacity))
    layer = Image.new('RGBA', mask.size, rgb(color) + (0,))
    layer.putalpha(m)
    return layer


def split_glyph(zh):
    """'静｜line' -> ('静', 'line'); no separator -> (None, zh)."""
    for sep in ('｜', '|'):
        if sep in zh:
            g, r = zh.split(sep, 1)
            return g.strip(), r.strip()
    return None, zh


def emph_runs(text, emph, default='rest'):
    """Split text into [(chars, level)] from an "emph" list: a string = level 'emph', a dict {'text', 'level'} =
    that level. Every non-overlapping occurrence is marked, earlier entries win.
    Returns (runs, missing): missing = entries that do not occur in text (or only inside an earlier mark)."""
    lv = [None] * len(text)
    missing = []
    for e in emph or []:
        t, L = (e, 'emph') if isinstance(e, str) else (str(e.get('text', '')), e.get('level', 'emph'))
        if not t:
            continue
        i, hit = text.find(t), False
        while i >= 0:
            if all(v is None for v in lv[i:i + len(t)]):
                lv[i:i + len(t)] = [L] * len(t)
                hit = True
            i = text.find(t, i + len(t))
        if not hit:
            missing.append(t)
    runs = []
    for ch, L in zip(text, lv):
        L = L or default
        if runs and runs[-1][1] == L:
            runs[-1][0] += ch
        else:
            runs.append([ch, L])
    return [tuple(r) for r in runs], missing


class _Line:
    """One typeset line: chars [(ch, font, tracking after it, rgba[, dx, advance])] on one baseline; width and ink
    extent (top < 0 < bot, relative to the baseline, measured on `ref` in every font used), all in supersampled
    px. dx / advance (optional) override where the glyph sits inside its slot and the slot width."""

    def __init__(self, chars, ref):
        self.chars = [c if len(c) == 6 else (*c, 0.0, None) for c in chars]
        self.w = sum(self.adv(c) for c in self.chars) + sum(c[2] for c in self.chars[:-1])
        fonts = {id(c[1]): c[1] for c in self.chars}.values()
        ext = [f.getbbox(ref, anchor='ls') for f in fonts]
        self.top = min((e[1] for e in ext), default=0)
        self.bot = max([0] + [e[3] for e in ext])
        self.h = self.bot - self.top

    @staticmethod
    def adv(c):
        return c[1].getlength(c[0]) if c[5] is None else c[5]

    def ops(self, x, base):
        out = []
        for c in self.chars:
            out.append((x + c[4], base, c[0], c[1], c[3]))
            x += self.adv(c) + c[2]
        return out


def _level(st, spec, name, SS):
    """(font, tracking px, ink key) of a caption level at supersampled size."""
    L = spec['levels'][name]
    px = st.fpx(L['px']) * SS
    dflt = st.font_en if name.startswith('en') else st.font_glyph if name == 'glyph' else st.font_zh
    f = st.font(st.path(L.get('font'), dflt), px)
    return f, float(L.get('tracking_em', 0)) * px, L.get('ink', 'en' if name.startswith('en') else 'zh')


_INK = {}


def ink_x(font, ch):
    """Horizontal ink extent (l, r) of one glyph relative to its origin, in the font's px. (Pillow's getbbox
    reports the advance box horizontally for CJK fonts, so it cannot be used to fit punctuation.)"""
    key = (font.path, font.index, font.size, ch)
    if key not in _INK:
        m, (ox, _) = font.getmask2(ch, anchor='ls')
        bb = m.getbbox()
        _INK[key] = (ox + bb[0], ox + bb[2]) if bb else (0.0, 0.0)
    return _INK[key]


def _zh_line(runs, st, spec, ink, SS):
    lv = spec['levels']
    pl = spec.get('punct_level', 'rest')
    memo = {}

    def get(n):
        if n not in memo:
            memo[n] = _level(st, spec, n, SS)
        return memo[n]
    pad_em = float(spec.get('punct_pad_em', 0.14))
    trail = spec.get('punct_trail') or {}
    t_chars = set(trail.get('chars', '')) if trail.get('on', True) else set()
    n_all = sum(len(t) for t, _ in runs)
    chars, i = [], 0
    for text, n in runs:
        n = n if n in lv else 'emph'
        fn, trn, key = get(n)
        col = rgb(ink.get(key) or ink['zh']) + (255,)
        for k, ch in enumerate(text):
            i += 1
            if ch in PUNCT and pl in lv and float(lv[n]['px']) > float(lv[pl]['px']) and k < len(text) - 1:
                # punctuation INSIDE a larger run (轻、静、快): set at the smaller punct level in the run's own ink,
                # its ink box in a narrow slot - pad_l_em before, pad_r_em after (of the run size; it leans on the
                # character it follows) - so the big characters keep an even rhythm instead of a full-width hole
                f, _, pkey = get(pl)
                l, r = ink_x(f, ch)
                pad_l = float(spec.get('punct_pad_l_em', pad_em)) * fn.size
                pad_r = float(spec.get('punct_pad_r_em', pad_em)) * fn.size
                pcol = col if spec.get('punct_ink', 'run') == 'run' else rgb(ink.get(pkey) or ink['zh']) + (255,)
                chars.append((ch, f, trn, pcol, pad_l - l, (r - l) + pad_l + pad_r))
            elif ch in t_chars:
                # any other pause mark (，、。…): compressed half-width slot - its ink plus pad_l_em before and
                # pad_r_em after (of its own size), none after the last mark of a line - instead of a full-em box
                # whose empty half reads as a hole after a large key word
                l, r = ink_x(fn, ch)
                pad_l = float(trail.get('pad_l_em', 0.06)) * fn.size
                pad_r = float(trail.get('pad_r_em', 0.42)) * fn.size if i < n_all else 0.0
                chars.append((ch, fn, trn, col, pad_l - l, (r - l) + pad_l + pad_r))
            else:
                chars.append((ch, fn, trn, col))
    return _Line(chars, spec.get('ref_glyph', '国'))     # 'Hg' for Latin type (preset latin)


def _en_line(en, en_emph, st, spec, ink, SS, track_em=None):
    """Secondary line; track_em overrides the tracking of both en levels (en_fit)."""
    up = spec.get('en_uppercase', True)
    marks = [{'text': t.upper() if up else t, 'level': 'en_emph'} for t in (en_emph or [])
             if 'en_emph' in spec['levels']]
    runs, missing = emph_runs(en, marks, default='en')
    chars = []
    for text, n in runs:
        f, tr, key = _level(st, spec, n, SS)
        if track_em is not None:
            tr = float(track_em) * f.size
        col = rgb(ink.get(key) or ink['en']) + (255,)
        chars += [(ch, f, tr, col) for ch in text]
    return _Line(chars, 'H' if en.isupper() else 'Hg'), missing


def _en_fit(en, en_emph, st, spec, ink, SS, target):
    """Secondary line with its tracking tightened (never below en_fit.min_tracking_em) so it is no wider than
    target (the main line's measure): the two lines lock up as one block."""
    el, miss = _en_line(en, en_emph, st, spec, ink, SS)
    fit = spec.get('en_fit') or {}
    if target and fit and fit.get('on', True) and el.w > target and len(el.chars) > 1:
        tr0 = float(spec['levels']['en'].get('tracking_em', 0.3))
        px = el.chars[0][1].size
        tr = max(float(fit.get('min_tracking_em', 0.18)), tr0 - (el.w - target) / (px * (len(el.chars) - 1)))
        el, miss = _en_line(en, en_emph, st, spec, ink, SS, track_em=tr)
    return el, miss


def _breaks(text, subs):
    """Wrap points of text: the end of the first occurrence of each substring in subs (str or list).
    Returns (sorted cut indices, substrings not found)."""
    subs = [subs] if isinstance(subs, str) else list(subs or [])
    cuts, missing = set(), []
    for b in subs:
        i = text.find(b) if b else -1
        if i < 0:
            missing.append(b)
        elif 0 < i + len(b) < len(text):
            cuts.add(i + len(b))
    return sorted(cuts), missing


def _cut_runs(runs, cuts):
    """Split [(text, level)] runs into lines at the char indices in cuts (leading spaces of a new line dropped)."""
    lines, cur, i = [], [], 0
    for t, L in runs:
        for ch in t:
            if i in cuts and cur:
                lines.append(cur)
                cur = []
            i += 1
            if not cur and ch == ' ':
                continue
            if cur and cur[-1][1] == L:
                cur[-1][0] += ch
            else:
                cur.append([ch, L])
    lines.append(cur)
    return [[tuple(r) for r in ln] for ln in lines if ln]


def _position(c, st):
    pos = c.get('pos') or 'lower_left'
    return pos if isinstance(pos, dict) else (st.d['positions'].get(pos) or st.d['positions']['lower_left'])


def _align(c, st):
    P = _position(c, st)
    return P.get('align') or ('center' if 'cx' in P else 'right' if 'right' in P else 'left')


def caption_layout(c, st):
    """Typeset one caption block (no drawing): returns (ops, rects, bw, bh, missing) in supersampled px relative
    to the block's ink box; ops = [(x, baseline, ch, font, rgba)], rects = [(x0, y0, x1, y1, rgba)]; missing =
    emph / en_emph / break substrings that are not in their line. Block alignment (left / center / right) comes
    from the caption's position. Optional "zh_break" / "en_break" (substring or list): wrap the main line (after
    the glyph) / the secondary line after that substring; wrapped main lines are zh_leading_px apart, secondary
    lines en_leading_px."""
    d, s, SS = st.d, st.s, st.ss
    cap = d['caption']
    ink = d['ink'].get(c.get('ink') or 'light') or d['ink']['light']
    zh, en = c.get('zh') or '', c.get('en') or ''
    up = cap.get('en_uppercase', True)
    if up:
        en = en.upper()
    shift = {'center': 0.5, 'right': 1.0}.get(_align(c, st), 0.0)
    ops, rects, missing = [], [], []
    hair = cap.get('hairline') or {}
    hw = max(1.0, float(hair.get('w_px', 2)) * s * SS)
    sp = float(hair.get('space_px', 24)) * s * SS
    rule = rgb(ink['zh']) + (round(255 * float(hair.get('opacity', 0.55))),)
    glyph = None
    zgap = float(cap.get('zh_leading_px', 16)) * s * SS
    egap = float(cap.get('en_leading_px', 10)) * s * SS
    if c.get('style') == 'chapter':
        ch = d['chapter']
        zs, es = st.fpx(ch['zh_px']) * SS, st.fpx(ch['en_px']) * SS
        fz, fe = st.font(st.path(ch.get('zh_font'), st.font_zh), zs), st.font(st.font_en, es)
        tz, te = float(ch.get('zh_tracking_em', 0.5)) * zs, float(ch.get('en_tracking_em', 0.45)) * es
        zls = [_Line([(x, fz, tz, rgb(ink['zh']) + (255,)) for x in zh], ch.get('ref_glyph', '国'))] if zh else []
        els = [_Line([(x, fe, te, rgb(ink['en']) + (255,)) for x in en], 'H')] if en else []
        gap = float(ch.get('line_gap_px', 20)) * s * SS
    else:
        glyph, rest = split_glyph(zh)
        runs, missing = emph_runs(rest, c.get('emph'))
        cuts, m_b = _breaks(rest, c.get('zh_break'))
        zls = [_zh_line(r, st, cap, ink, SS) for r in _cut_runs(runs, cuts)] if rest else []
        eb = c.get('en_break')
        eb = [eb] if isinstance(eb, str) else list(eb or [])
        ecuts, m_e = _breaks(en, [b.upper() if up else b for b in eb])
        parts = [en[a:b].strip() for a, b in zip([0] + ecuts, ecuts + [len(en)])] if en else []
        parts = [p for p in parts if p]
        target = max((z.w for z in zls), default=None)
        els, m_en = [], None
        for part in parts:
            el, m2 = _en_fit(part, c.get('en_emph'), st, cap, ink, SS, target)
            els.append(el)
            m_en = set(m2) if m_en is None else (m_en & set(m2))     # missing only if no line has it
        missing += sorted(m_en or []) + m_b + m_e
        gap = float(cap.get('line_gap_px', 20)) * s * SS
    layout = c.get('layout') or cap.get('layout', 'drop')
    gl = _zh_line([(glyph, 'glyph')], st, cap, ink, SS) if glyph else None
    inline = bool(gl) and layout != 'drop'
    # vertical stack: (line, top of its ink box); inline: the first main line shares the glyph's baseline
    placed, y, g_base = [], 0.0, None
    for k, z in enumerate(zls):
        if k:
            y += zgap
        if k == 0 and inline:
            t0 = min(gl.top, z.top)
            placed.append((z, y - t0 + z.top))
            g_base = y - t0
            y += max(gl.bot, z.bot) - t0
        else:
            placed.append((z, y))
            y += z.h
    if els and zls:
        y += gap
    for k, e in enumerate(els):
        if k:
            y += egap
        placed.append((e, y))
        y += e.h
    st_h = y
    top, bot = 0.0, st_h
    if gl:
        xs = gl.w + 2 * sp + hw
        if inline:
            if g_base is None:                                # glyph alone (no main line after it)
                g_base = -gl.top
                bot = max(bot, gl.h)
            ops += gl.ops(0.0, g_base)
            rf = _level(st, cap, 'rest', SS)[0]
            _, rt, _, rb = rf.getbbox(cap.get('ref_glyph', '国'), anchor='ls')
            hh = float(hair.get('h_em', 0.78)) * rf.size
            cy = g_base + (rt + rb) / 2
            rects.append((gl.w + sp, cy - hh / 2, gl.w + sp + hw, cy + hh / 2, rule))
        else:                                                 # drop cap: glyph ink centred on the whole stack
            g_base = st_h / 2 - (gl.top + gl.bot) / 2
            top, bot = min(0.0, g_base + gl.top), max(st_h, g_base + gl.bot)
            ops += gl.ops(0.0, g_base)
            span = max(st_h, gl.h) * float(hair.get('span', 1.0))
            rects.append((gl.w + sp, st_h / 2 - span / 2, gl.w + sp + hw, st_h / 2 + span / 2, rule))
        for ln, yt in placed:
            ops += ln.ops(xs, yt - ln.top)
        bw = xs + max((ln.w for ln, _ in placed), default=0.0)
    else:                                                     # stacked lines, aligned per the block
        bw = max((ln.w for ln, _ in placed), default=0.0)
        for ln, yt in placed:
            ops += ln.ops((bw - ln.w) * shift, yt - ln.top)
    if top:
        ops = [(x, b - top, *r) for x, b, *r in ops]
        rects = [(x0, y0 - top, x1, y1 - top, col) for x0, y0, x1, y1, col in rects]
    return ops, rects, bw, bot - top, missing


def caption_place(c, st, bw_t, bh_t):
    """Top-left (output px) of a block of bw_t x bh_t output px at the caption's position, kept inside the safe
    margin. x / right / top / bottom are scaled margins; cx / cy are reference-frame coordinates mapped
    proportionally onto the output frame."""
    s = st.s
    P = _position(c, st)
    if 'cx' in P:
        bx = float(P['cx']) * st.W / st.refW - bw_t / 2
    elif 'right' in P:
        bx = st.W - float(P['right']) * s - bw_t
    else:
        bx = float(P.get('x', 120)) * s
    if 'top' in P:
        by = float(P['top']) * s
    elif 'cy' in P:
        by = float(P['cy']) * st.H / st.refH - bh_t / 2
    else:
        by = st.H - float(P.get('bottom', 104)) * s - bh_t
    m = float(st.d.get('safe_px', 48)) * s
    bx = min(max(bx, m), max(m, st.W - m - bw_t))
    by = min(max(by, m), max(m, st.H - m - bh_t))
    return bx, by


def caption_fit(c, st):
    """Scale factor (<= 1) that makes the caption block fit the frame width minus the safe margins."""
    _, _, bw, _, _ = caption_layout(c, st)
    avail = st.W - 2 * float(st.d.get('safe_px', 48)) * st.s
    return min(1.0, avail / (bw / st.ss)) if bw > 0 else 1.0


def caption_measure(c, st):
    """Layout and placement of one caption exactly as caption_png draws it, without drawing: returns
    (style variant, fit scale k, ops, rects, bw, bh, missing, bx, by); bw / bh in supersampled px, bx / by = the
    block's top-left in output px."""
    st = st.variant(c.get('preset'))
    k = caption_fit(c, st)
    if k < 0.999:
        with st.scaled(k):
            ops, rects, bw, bh, missing = caption_layout(c, st)
    else:
        ops, rects, bw, bh, missing = caption_layout(c, st)
    bx, by = caption_place(c, st, bw / st.ss, bh / st.ss)
    return st, k, ops, rects, bw, bh, missing, bx, by


def caption_box(c, st):
    """Output-px block box (x, y, w, h) of a caption as it will be drawn (safe-box checks, placement scoring)."""
    st, _, _, _, bw, bh, _, bx, by = caption_measure(c, st)
    return bx, by, bw / st.ss, bh / st.ss


def caption_png(c, st, out_png, luma=None):
    """Render one caption block. c: {zh, en, pos, ink, style, emph?, en_emph?, layout?, preset?}. luma(box)->0..1
    mean luma of the frame under box (output px) for the scrim rule. Returns placement and decisions."""
    st, k, ops, rects, bw, bh, missing, bx, by = caption_measure(c, st)
    d, s, SS = st.d, st.s, st.ss
    chapter = c.get('style') == 'chapter'
    spec = deep_merge(d['caption'], d['chapter']) if chapter else d['caption']
    ink_name = c.get('ink') or 'light'
    ink = d['ink'].get(ink_name) or d['ink']['light']
    bw_t, bh_t = bw / SS, bh / SS

    sc = d.get('scrim_params') or {}
    pad = float(sc.get('pad_px', 60)) * s
    mean = None
    scrim = False
    if luma is not None and ink_name != 'dark' and sc:
        mean = luma((bx - pad, by - pad, bx + bw_t + pad, by + bh_t + pad))
        scrim = mean is not None and mean > float(sc.get('threshold', 0.3))
    sh = ink.get('shadow')
    sig_sh = float(sh['blur_px']) / 2 * s * SS if sh else 0
    sig_sc = float(sc.get('feather_px', 70)) / 2.56 * s * SS
    m_ss = max(3 * sig_sh + (abs(float(sh.get('dy_px', 0))) * s * SS if sh else 0),
               (pad * SS + 3 * sig_sc) if scrim else 0)
    M = math.ceil(m_ss / SS) + 2
    ox, oy = math.floor(bx) - M, math.floor(by) - M
    wt, ht = math.ceil(bw_t) + 2 * M + 2, math.ceil(bh_t) + 2 * M + 2
    img, mask = _canvas(wt, ht, SS)
    dtext, dmask = ImageDraw.Draw(img), ImageDraw.Draw(mask)
    x0, y0 = (bx - ox) * SS, (by - oy) * SS
    for rx0, ry0, rx1, ry1, col in rects:
        box = [x0 + rx0, y0 + ry0, x0 + rx1 - 1, y0 + ry1]
        dtext.rectangle(box, fill=col)
        dmask.rectangle(box, fill=col[3])
    for x, base, ch, f, col in ops:
        dtext.text((x0 + x, y0 + base), ch, font=f, fill=col, anchor='ls')
        dmask.text((x0 + x, y0 + base), ch, font=f, fill=255, anchor='ls')

    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    if scrim:
        box = Image.new('L', img.size, 0)
        ImageDraw.Draw(box).rectangle([x0 - pad * SS, y0 - pad * SS, x0 + bw + pad * SS, y0 + bh + pad * SS], fill=255)
        out = Image.alpha_composite(out, _soft(box, sig_sc, float(sc.get('opacity', 0.3)), '#000000'))
    if sh:
        out = Image.alpha_composite(out, _soft(mask, sig_sh, float(sh.get('opacity', 0.5)), sh.get('color', '#000'),
                                               (0, float(sh.get('dy_px', 0)) * s * SS)))
    out = Image.alpha_composite(out, img)
    out = out.resize((wt, ht), LANCZOS)
    out.save(out_png)
    return dict(png=str(out_png), x=ox, y=oy, w=wt, h=ht, block=[round(bx, 1), round(by, 1), round(bw_t, 1),
                round(bh_t, 1)], align=_align(c, st), scrim=scrim, luma=None if mean is None else round(mean, 3),
                chapter=chapter, rise=float(d.get('rise_px', 6)) * s, missing_emph=missing,
                fit_scale=round(k, 3) if k < 0.999 else None,
                fade=(float(spec.get('fade_in', d.get('fade_in', 0.4))), float(spec.get('fade_out', d.get('fade_out', 0.4)))))


def card_png(card, st, out_png):
    """Full-frame card (RGB). card: {style: title|chapter|end, mark?, zh?, en?, note? (small print at the bottom),
    preset?}. Same hierarchy as the captions: the mark (a logo word) leads; in the main line the cards
    channel_chars take the caption glyph colour and weight and punctuation ('·') a dim accent, the rest is CJK
    Light; the secondary line is small tracked caps. A line wider than the frame minus 2 x cards.side_px is scaled
    down to fit."""
    st = st.variant(card.get('preset'))
    d, s, SS, W, H = st.d, st.s, st.ss, st.W, st.H
    C = d['cards']
    style = card.get('style') or 'title'
    mark, zh, en = card.get('mark') or '', card.get('zh') or '', card.get('en') or ''
    lines = []            # ('t', [(ch, font, rgb)], tracking_px, ref) | ('gap', px) | ('rule',)
    rule = C.get('rule') or {}
    chan = set(C.get('channel_chars') or '')
    ch_spec = C.get('channel') or {}

    zref = card.get('zh_ref') or C.get('zh_ref', '国')
    ztr = float(card.get('zh_tracking_em', C['zh']['tracking_em']))

    def zh_chars(text, px, color):
        f = st.font(st.path(card.get('zh_font'), C['zh'].get('font'), st.font_zh), px)
        fc = st.font(st.path(ch_spec.get('font'), st.font_glyph), px)
        out = []
        for c in text:
            if c in chan:
                out.append((c, fc, rgb(ch_spec.get('color', '#CDAE7E'))))
            elif c in PUNCT:
                out.append((c, f, rgb(C.get('sep_color', color))))
            else:
                out.append((c, f, rgb(color)))
        return out

    def plain(text, font_key, default_font, px, color):
        f = st.font(st.path(C[font_key].get('font'), default_font), px)
        return [(c, f, rgb(color)) for c in text]

    if style == 'chapter':
        ch = d['chapter']
        if zh:
            px = st.fpx(ch['zh_px']) * SS
            lines.append(('t', zh_chars(zh, px, C['zh']['color']), ch['zh_tracking_em'] * px, zref))
        if en:
            px = st.fpx(ch['en_px']) * SS
            lines.append(('gap', float(ch.get('line_gap_px', 20))))
            lines.append(('t', plain(en.upper(), 'en', st.font_en, px, C['en']['color']),
                          ch['en_tracking_em'] * px, 'H'))
    else:
        if mark:
            if style in (rule.get('on') or []):
                lines.append(('rule',))
                lines.append(('gap', float(rule.get('above_mark_px', 28))))
            px = st.fpx(card.get('mark_px') or C['mark']['px']) * SS       # per-card override for a longer mark
            lines.append(('t', plain(mark.upper(), 'mark', st.font_en, px, C['mark']['color']),
                          float(card.get('mark_tracking_em', C['mark']['tracking_em'])) * px, 'H'))
            if zh or en:
                lines.append(('gap', float(C.get('gap_mark_zh_px', 40))))
        if zh:
            zpx = card.get('zh_px') or (C.get('end_zh_px', C['zh']['px']) if style == 'end' else C['zh']['px'])
            if not mark:
                zpx = float(zpx) * float(C.get('no_mark_zh_scale', 1.6))
                if style in (rule.get('on') or []):
                    lines += [('rule',), ('gap', float(rule.get('above_mark_px', 28)))]
            px = st.fpx(zpx) * SS
            lines.append(('t', zh_chars(zh, px, C['zh']['color']), ztr * px, zref))
            if en:
                lines.append(('gap', float(C.get('gap_zh_en_px', 22))))
        if en:
            px = st.fpx(C['en']['px']) * SS
            lines.append(('t', plain(en.upper(), 'en', st.font_en, px, C['en']['color']), C['en']['tracking_em'] * px,
                          'H'))
    # measure the column; a line wider than the frame minus the side margins is scaled down
    maxw = (W - 2 * float(C.get('side_px', 96)) * s) * SS
    rows, total, fitted = [], 0.0, []
    for ln in lines:
        if ln[0] == 'gap':
            rows.append(('gap', ln[1] * s * SS))
        elif ln[0] == 'rule':
            rows.append(('rule', max(1.0, float(rule.get('h_px', 1)) * s * SS)))
        else:
            _, chars, tr, ref = ln
            w = sum(f.getlength(c) for c, f, _ in chars) + tr * (len(chars) - 1)
            if w > maxw > 0:
                k = maxw / w
                chars = [(c, st.font_like(f, f.size * k), col) for c, f, col in chars]
                tr *= k
                w = sum(f.getlength(c) for c, f, _ in chars) + tr * (len(chars) - 1)
                fitted.append(round(k, 3))
            ext = [f.getbbox(ref, anchor='ls') for f in {id(f): f for _, f, _ in chars}.values()]
            t, b = min(e[1] for e in ext), max(e[3] for e in ext)
            rows.append(('t', b - t, chars, tr, t, w))
        total += rows[-1][1]
    img = Image.new('RGB', (W * SS, H * SS), rgb(C.get('bg', '#000000')))
    dr = ImageDraw.Draw(img)
    y = (H * SS - total) / 2
    for r in rows:
        if r[0] == 'rule':
            rw = float(rule.get('w_px', 64)) * s * SS
            dr.rectangle([(W * SS - rw) / 2, y, (W * SS + rw) / 2, y + r[1] - 1], fill=rgb(rule.get('color', '#C9A97A')))
        elif r[0] == 't':
            _, h, chars, tr, t, w = r
            x = (W * SS - w) / 2
            for c, f, col in chars:
                dr.text((x, y - t), c, font=f, fill=col, anchor='ls')
                x += f.getlength(c) + tr
        y += r[1]
    note = card.get('note') or ''
    if note:                    # fine print at the bottom of the card (AI notice, music credit); a newline = more lines
        N = C.get('note') or {}
        f0 = st.font(st.path(N.get('font'), st.font_zh), st.fpx(N.get('px', 22)) * SS)
        yb = H * SS - float(N.get('bottom', 72)) * s * SS
        for ln in reversed(note.splitlines()):          # bottom line first, stacking upwards
            f = f0
            tr = float(N.get('tracking_em', 0.1)) * f.size
            w = run_width(ln, f, tr)
            if w > maxw > 0:
                f = st.font_like(f0, f0.size * maxw / w)
                tr = float(N.get('tracking_em', 0.1)) * f.size
                w = run_width(ln, f, tr)
                fitted.append('note')
            x = (W * SS - w) / 2
            for c in ln:
                dr.text((x, yb), c, font=f, fill=rgb(N.get('color', '#7D776E')), anchor='ls')
                x += f.getlength(c) + tr
            yb -= f0.size * float(N.get('line_gap', 1.6))
    img.resize((W, H), LANCZOS).save(out_png)
    return dict(png=str(out_png), bg=C.get('bg', '#000000'), fitted=fitted)


def card_note_box(card, st):
    """Output-px box (x0, y0, x1, y1) of a card's small print ("note") as card_png places it; None without a note."""
    note = card.get('note') or ''
    if not note:
        return None
    st = st.variant(card.get('preset'))
    d, s, SS, W, H = st.d, st.s, st.ss, st.W, st.H
    C = d['cards']
    N = C.get('note') or {}
    maxw = (W - 2 * float(C.get('side_px', 96)) * s) * SS
    f0 = st.font(st.path(N.get('font'), st.font_zh), st.fpx(N.get('px', 22)) * SS)
    tr = float(N.get('tracking_em', 0.1)) * f0.size
    yb = H * SS - float(N.get('bottom', 72)) * s * SS
    x0, x1, top, bot = W * SS, 0.0, yb, yb
    for ln in reversed(note.splitlines()):
        w = run_width(ln, f0, tr)
        w = min(w, maxw) if maxw > 0 else w
        _, t, _, b = f0.getbbox(ln or ' ', anchor='ls')
        x0, x1 = min(x0, (W * SS - w) / 2), max(x1, (W * SS + w) / 2)
        top, bot = min(top, yb + t), max(bot, yb + b)
        yb -= f0.size * float(N.get('line_gap', 1.6))
    return tuple(round(v / SS, 1) for v in (x0, top, x1, bot))


def slate_png(text, st, out_png):
    """Black frame with a centred note (a clip whose source is missing keeps its slot on the timeline)."""
    SS, W, H = st.ss, st.W, st.H
    f = st.font(st.font_zh, 0.042 * min(W, H) * SS)
    img = Image.new('RGB', (W * SS, H * SS), (0, 0, 0))
    dr = ImageDraw.Draw(img)
    w = min(f.getlength(text), W * SS * 0.9)
    if f.getlength(text) > W * SS * 0.9:
        f = st.font_like(f, f.size * W * SS * 0.9 / f.getlength(text))
    _, t, _, b = f.getbbox('国', anchor='ls')
    dr.text(((W * SS - w) / 2, (H * SS - (b - t)) / 2 - t), text, font=f, fill=(204, 204, 204), anchor='ls')
    img.resize((W, H), LANCZOS).save(out_png)
    return dict(png=str(out_png))


def label_png(text, st, out_png):
    """Corner label (e.g. an AI-generated-footage notice) at bottom right, or top right when label.top is set."""
    d, s, SS = st.d, st.s, st.ss
    L = d['label']
    f = st.font(st.path(L.get('font'), st.font_zh), st.fpx(L.get('px', 24)) * SS)
    tr = float(L.get('tracking_em', 0)) * f.size
    w = run_width(text, f, tr)
    _, t, _, b = f.getbbox('国', anchor='ls')
    M = 3
    wt, ht = math.ceil(w / SS) + 2 * M, math.ceil((b - t) / SS) + 2 * M
    img, mask = _canvas(wt, ht, SS)
    a = round(255 * float(L.get('opacity', 0.6)))
    draw_run([(ImageDraw.Draw(img), None), (ImageDraw.Draw(mask), a)], M * SS, M * SS - t, text, f, tr,
             rgb(L.get('color', '#FFFFFF')) + (a,))
    out = Image.alpha_composite(_soft(mask, 1.2 * SS * max(s, 0.5), float(L.get('shadow', 0.4)), '#000000',
                                      (0, max(1.0, s * SS))), img)
    out.resize((wt, ht), LANCZOS).save(out_png)
    x = st.W - float(L.get('right', 44)) * s - wt + M
    if L.get('top') is not None:
        y = float(L['top']) * s - M
    else:
        y = st.H - float(L.get('bottom', 34)) * s - ht + M
    return dict(png=str(out_png), x=round(x), y=round(y), w=wt, h=ht)


# ----------------------------------------------------------------------------------------------- callouts

def _clamp01(u):
    return min(1.0, max(0.0, u))


def _sstep(u):
    u = _clamp01(u)
    return u * u * (3 - 2 * u)


def _ease_out(u):
    u = _clamp01(u)
    return 1 - (1 - u) ** 3


def callout_route(A, L, side, route='auto', knee=44.0):
    """Leader polyline (output px) from anchor A to the label point L; side +1 = label right of A, -1 = left.
    route: straight (A -> L), elbow (A -> knee -> horizontal shelf of `knee` px -> L), auto (straight when A and L
    are level within 1 px, else elbow). Explicit waypoints are resolved by the caller (item 'pts')."""
    if route == 'straight' or (route == 'auto' and abs(A[1] - L[1]) < 1.0):
        return [tuple(A), tuple(L)]
    K = (L[0] - side * knee, L[1])
    if (K[0] - A[0]) * side <= 0:            # anchor already beyond the knee: no room for a shelf
        return [tuple(A), tuple(L)]
    return [tuple(A), K, tuple(L)]


def _poly_len(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _poly_partial(pts, length):
    """Leading part of a polyline up to `length` px."""
    out, left = [pts[0]], length
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b)
        if d <= 1e-9:
            continue
        if left >= d:
            out.append(b)
            left -= d
            continue
        f = left / d
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        break
    return out


def _seg_cross(p1, p2, q1, q2):
    """Proper intersection of two segments (touching end points do not count)."""
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    return o(p1, p2, q1) * o(p1, p2, q2) < 0 and o(q1, q2, p1) * o(q1, q2, p2) < 0


def _seg_hits_box(p, q, box):
    x0, y0, x1, y1 = box
    if max(p[0], q[0]) < x0 or min(p[0], q[0]) > x1 or max(p[1], q[1]) < y0 or min(p[1], q[1]) > y1:
        return False
    if (x0 <= p[0] <= x1 and y0 <= p[1] <= y1) or (x0 <= q[0] <= x1 and y0 <= q[1] <= y1):
        return True
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return any(_seg_cross(p, q, a, b) for a, b in edges)


def _boxes_overlap(a, b, gap=0.0):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _paste_clipped(dst, img, x, y):
    """alpha_composite img onto dst at integer (x, y), clipping whatever falls outside dst."""
    x, y = int(x), int(y)
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(img.width, dst.width - x), min(img.height, dst.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    part = img if (sx0, sy0, sx1, sy1) == (0, 0, img.width, img.height) else img.crop((sx0, sy0, sx1, sy1))
    dst.alpha_composite(part, (x + sx0, y + sy0))


def _scale_alpha(img, a):
    if a >= 0.999:
        return img
    r = img.copy()
    r.putalpha(img.getchannel('A').point(lambda v: round(v * a)))
    return r


def _callout_text_lines(item, st, C):
    """Rows (supersampled fonts/metrics) of one label: name, function line, optional English; plus the block
    width/height and the first line's centre measured from the block top (SS px)."""
    SS, s = st.ss, st.s
    floor = float(C.get('min_px', 7))
    rows = []
    for key in ('name', 'fn', 'en'):
        text = item.get('zh' if key == 'name' else key) or ''
        if not text:
            continue
        spec = C[key]
        if key == 'en' and spec.get('uppercase', True):
            text = text.upper()
        px = max(floor, float(spec['px']) * s) * SS
        dflt = st.font_en if key == 'en' else st.font_glyph if key == 'name' else st.font_zh
        f = st.font(st.path(spec.get('font'), dflt), px)
        _, t, _, b = f.getbbox('H' if key == 'en' else C.get('ref_glyph', '国'), anchor='ls')
        tr = float(spec.get('tracking_em', 0)) * px
        rows.append(dict(key=key, text=text, font=f, track=tr, top=t, bot=b, w=run_width(text, f, tr)))
    fit = C.get('en_fit') or {}
    zw = max((r['w'] for r in rows if r['key'] != 'en'), default=0.0)
    for r in rows:      # as in the captions: the tracked English never outruns the name lines (en_fit)
        if r['key'] == 'en' and fit.get('on', True) and zw and r['w'] > zw and len(r['text']) > 1:
            px = r['font'].size
            r['track'] = max(float(fit.get('min_tracking_em', 0.18)) * px, r['track'] - (r['w'] - zw) / (len(r['text']) - 1))
            r['w'] = run_width(r['text'], r['font'], r['track'])
    gaps = {'fn': float(C.get('gap_name_fn_px', 10)), 'en': float(C.get('gap_fn_en_px', 8))}
    y = 0.0
    for i, r in enumerate(rows):
        if i:
            y += gaps.get(r['key'], 8) * s * SS
        r['y'] = y                                   # top of the row's ink box
        y += r['bot'] - r['top']
    bw = max((r['w'] for r in rows), default=0.0)
    mid = (rows[0]['bot'] - rows[0]['top']) / 2 if rows else 0.0
    return rows, bw, y, mid


def _callout_text_png(it, ink, C, st, scrim):
    """Label block (+ soft shadow, optional feathered scrim) as RGBA at output size and its integer offset."""
    SS, s = st.ss, st.s
    rows, bw, bh, bx, by, align = it['rows'], it['bw'], it['bh'], it['bx'], it['by'], it['align']
    sh = C.get('text_shadow') or {}
    use_sh = bool(sh) and ink.get('shadow') is not None
    sig = float(sh.get('blur_px', 9)) / 2 * s * SS if use_sh else 0.0
    sc = C.get('scrim') or {}
    pad = float(sc.get('pad_px', 34)) * s
    sig_sc = float(sc.get('feather_px', 46)) / 2.56 * s * SS
    m = max(3 * sig + (abs(float(sh.get('dy_px', 2))) * s * SS if use_sh else 0), (pad * SS + 3 * sig_sc) if scrim else 0)
    M = math.ceil(m / SS) + 2
    ox, oy = math.floor(bx) - M, math.floor(by) - M
    wt, ht = math.ceil(bw / SS) + 2 * M + 2, math.ceil(bh / SS) + 2 * M + 2
    img, mask = _canvas(wt, ht, SS)
    dt, dm = ImageDraw.Draw(img), ImageDraw.Draw(mask)
    x0, y0 = (bx - ox) * SS, (by - oy) * SS
    for r in rows:
        shift = {'right': bw - r['w'], 'center': (bw - r['w']) / 2}.get(align, 0.0)
        draw_run([(dt, None), (dm, 255)], x0 + shift, y0 + r['y'] - r['top'], r['text'], r['font'], r['track'],
                 rgb(ink[r['key']]) + (255,))
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    if scrim:
        box = Image.new('L', img.size, 0)
        ImageDraw.Draw(box).rectangle([x0 - pad * SS, y0 - pad * SS, x0 + bw + pad * SS, y0 + bh + pad * SS], fill=255)
        out = Image.alpha_composite(out, _soft(box, sig_sc, float(sc.get('opacity', 0.28)), '#000000'))
    if use_sh:
        out = Image.alpha_composite(out, _soft(mask, sig, float(sh.get('opacity', 0.5)), ink['shadow'],
                                               (0, float(sh.get('dy_px', 2)) * s * SS)))
    return Image.alpha_composite(out, img).resize((wt, ht), LANCZOS), ox, oy


def callout_layout(group, st):
    """Geometry of a callout group in output px. group['items'][i]: {id, A: (x, y) anchor, L: (x, y) label point,
    zh, fn, en, route?, pts?}. Adds side, pts (leader polyline A..L), line_pts (visible part, starting at the
    ring), the text rows and text_box; returns the layout warnings (callout_check)."""
    C = st.d['callout']
    s, SS = st.s, st.ss
    rr = float(C.get('ring_r_px', 10)) * s + 0.5
    for it in group['items']:
        A, L = tuple(it['A']), tuple(it['L'])
        side = it.get('side') or (1 if L[0] >= A[0] else -1)
        side = {'right': 1, 'left': -1}.get(side, side)
        pts = [tuple(p) for p in it['pts']] if it.get('pts') else \
            callout_route(A, L, side, it.get('route', 'auto'), float(C.get('knee_px', 44)) * s)
        a, b = pts[0], pts[1]
        d = math.dist(a, b)
        start = (a[0] + (b[0] - a[0]) * rr / d, a[1] + (b[1] - a[1]) * rr / d) if d > rr else a
        rows, bw, bh, mid = _callout_text_lines(it, st, C)
        gap = float(C.get('text_gap_px', 14)) * s
        bx = L[0] + gap if side > 0 else L[0] - gap - bw / SS
        by = L[1] - mid / SS
        it.update(A=A, L=L, side=side, pts=pts, line_pts=[start] + pts[1:], rows=rows, bw=bw, bh=bh, bx=bx, by=by,
                  align='left' if side > 0 else 'right', text_box=(bx, by, bx + bw / SS, by + bh / SS))
    return callout_check(group, st)


def callout_check(group, st, label_zone=None):
    """Layout rules: leader lines never cross each other, labels never overlap each other or another item's
    leader, labels stay inside the safe margin (and clear of label_zone, the corner label box, if given)."""
    C = st.d['callout']
    items, warn = group['items'], []
    m = float(C.get('margin_px', 48)) * st.s
    for i, a in enumerate(items):
        tb = a['text_box']
        if tb[0] < m or tb[1] < m * 0.6 or tb[2] > st.W - m or tb[3] > st.H - m * 0.6:
            warn.append(f'callout {a["id"]}: label box {tuple(round(v) for v in tb)} is inside the {round(m)} px '
                        f'safe margin (or outside the frame)')
        if label_zone and _boxes_overlap(tb, label_zone):
            warn.append(f'callout {a["id"]}: label overlaps the corner label')
        for b in items[i + 1:]:
            if _boxes_overlap(tb, b['text_box'], 4 * st.s):
                warn.append(f'callouts {a["id"]} / {b["id"]}: label boxes overlap')
            if any(_seg_cross(p, q, u, v) for p, q in zip(a['pts'], a['pts'][1:])
                   for u, v in zip(b['pts'], b['pts'][1:])):
                warn.append(f'callouts {a["id"]} / {b["id"]}: leader lines cross')
        for b in items:
            if b is not a and any(_seg_hits_box(p, q, tb) for p, q in zip(b['pts'], b['pts'][1:])):
                warn.append(f'callout {b["id"]}: leader line runs through the label of {a["id"]}')
    return warn


def callout_frames(group, st, out_dir, fps, luma=None, label_zone=None):
    """Render one callout group as an RGBA PNG sequence f_0000.png.. cropped to the union box of its items.
    group: {'items': [{id, A, L, zh, fn, en, ink: light|dark|auto, route?, t0: start s from the group start}],
            'dur': s (to the end of the fade-out), 'fade_out': s?}. Frame k = group time k / fps.
    luma(box) -> mean luma (0-1) of the picture under box (output px) at full state: ink 'auto' picks dark ink
    above auto_ink_luma (median over the group's 'auto' labels, so one set never mixes inks); light ink above the
    scrim threshold gets a feathered scrim.
    Per item: the dot pops in and its ring expands, the leader draws from the anchor to the label (ease-out),
    then the label fades in while gliding slide_px outward; the whole group fades out together."""
    import io
    C = st.d['callout']
    T = C.get('timing') or {}
    s, SS = st.s, 4
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob('f_*.png'):
        f.unlink()
    warns = callout_layout(group, st)
    if label_zone:
        warns += [w for w in callout_check(group, st, label_zone) if 'corner label' in w]
    lw = max(1.0, float(C.get('line_w_px', 1.5)) * s)
    r_dot = max(1.6, float(C.get('dot_r_px', 4)) * s)
    r_ring = max(r_dot + 1.5, float(C.get('ring_r_px', 10)) * s)
    w_ring = max(0.8, float(C.get('ring_w_px', 1.2)) * s)
    de = C.get('dot_edge') or {}
    edge_w, edge_a = max(0.6, float(de.get('w_px', 0)) * s) if de.get('w_px') else 0.0, float(de.get('opacity', 0))
    lsh = C.get('line_shadow') or {}
    sig_l, dy_l = float(lsh.get('blur_px', 4)) / 2 * s, float(lsh.get('dy_px', 1)) * s
    slide = float(C.get('slide_px', 8)) * s
    dot_s, ring_s = float(T.get('dot_s', 0.2)), float(T.get('ring_s', 0.55))
    l_delay, draw_s = float(T.get('line_delay_s', 0.06)), float(T.get('draw_s', 0.3))
    t_delay, t_fade = float(T.get('text_delay_s', 0.26)), float(T.get('text_fade_s', 0.4))
    dur = float(group['dur'])
    fo = min(float(group.get('fade_out') or T.get('fade_out_s', 0.5)), dur / 3)
    n = max(2, round(dur * fps))
    sc = C.get('scrim') or {}
    gl = math.ceil(slide) + 1

    pad = math.ceil(r_ring + 3 * sig_l + abs(dy_l) + lw + 2)
    # ink 'auto': one ink for the whole group (median background luma under the 'auto' labels), so a set of
    # labels never mixes light and dark type; callout.group_ink False = decide per label
    thr = float(C.get('auto_ink_luma', 0.36))
    lums = {it['id']: (luma(it['text_box']) if luma else None) for it in group['items']}
    vals = sorted(lums[it['id']] for it in group['items']
                  if (it.get('ink') or 'auto') == 'auto' and lums[it['id']] is not None)
    group_ink = None
    if vals and C.get('group_ink', True):
        group_ink = 'dark' if vals[len(vals) // 2] > thr else 'light'
    for it in group['items']:
        ink_name = it.get('ink') or 'auto'
        lum = lums[it['id']]
        if ink_name == 'auto':
            ink_name = group_ink or ('dark' if (lum is not None and lum > thr) else 'light')
        ink = C['ink'][ink_name]
        scrim = False
        if ink_name == 'light' and luma and sc:
            p = float(sc.get('pad_px', 34)) * s
            tb = it['text_box']
            ml = luma((tb[0] - p, tb[1] - p, tb[2] + p, tb[3] + p))
            scrim = ml is not None and ml > float(sc.get('threshold', 0.34))
        img, ox, oy = _callout_text_png(it, ink, C, st, scrim)
        glide = Image.new('RGBA', (img.width + gl, img.height), (0, 0, 0, 0))   # room for the glide
        glide.paste(img, (gl, 0) if it['side'] > 0 else (0, 0))
        xs, ys = [p[0] for p in it['pts']], [p[1] for p in it['pts']]
        it.update(ink_used=ink_name, luma=None if lum is None else round(lum, 3), scrim=scrim,
                  txt=img, txt_xy=(ox, oy), glide=glide, glide_xy=(ox - gl, oy) if it['side'] > 0 else (ox, oy),
                  lbox=(math.floor(min(xs) - pad), math.floor(min(ys) - pad), math.ceil(max(xs) + pad),
                        math.ceil(max(ys) + pad)),
                  plen=_poly_len(it['line_pts']), col={k: rgb(v) for k, v in ink.items() if isinstance(v, str)},
                  shadow=ink.get('shadow'))

    boxes = [it['lbox'] for it in group['items']] + \
            [(*it['glide_xy'], it['glide_xy'][0] + it['glide'].width, it['glide_xy'][1] + it['glide'].height)
             for it in group['items']]
    ux0, uy0 = max(0, min(b[0] for b in boxes)), max(0, min(b[1] for b in boxes))
    ux1, uy1 = min(st.W, max(b[2] for b in boxes)), min(st.H, max(b[3] for b in boxes))
    if ux1 - ux0 < 2 or uy1 - uy0 < 2:          # everything outside the frame (e.g. cropped away in a vertical cut)
        ux0, uy0, ux1, uy1 = 0, 0, 2, 2
        warns.append('callout group lies entirely outside the frame')

    def line_layer(it, u_dot, u_ring, u_line):
        """Dot + ring + partial leader (+ soft shadow) as RGBA at lbox size."""
        x0, y0, x1, y1 = it['lbox']
        w, h = x1 - x0, y1 - y0
        P = lambda p: ((p[0] - x0) * SS, (p[1] - y0) * SS)
        masks = {k: Image.new('L', (w * SS, h * SS), 0) for k in ('line', 'ring', 'edge', 'dot')}
        c = P(it['A'])
        if u_line > 0:
            pts = _poly_partial(it['line_pts'], it['plen'] * u_line)
            if len(pts) >= 2 and _poly_len(pts) > 0.05:
                ImageDraw.Draw(masks['line']).line([P(p) for p in pts], fill=255, width=max(1, round(lw * SS)),
                                                   joint='curve')
        if u_ring > 0:
            rr = (r_dot + (r_ring - r_dot) * _ease_out(u_ring)) * SS
            ImageDraw.Draw(masks['ring']).ellipse([c[0] - rr, c[1] - rr, c[0] + rr, c[1] + rr], outline=255,
                                                  width=max(1, round(w_ring * SS)))
        if u_dot > 0:
            rd = r_dot * (0.35 + 0.65 * _ease_out(u_dot)) * SS
            ImageDraw.Draw(masks['dot']).ellipse([c[0] - rd, c[1] - rd, c[0] + rd, c[1] + rd], fill=255)
            if it['shadow'] and edge_w > 0:
                re = rd + edge_w * SS
                ImageDraw.Draw(masks['edge']).ellipse([c[0] - re, c[1] - re, c[0] + re, c[1] + re], fill=255)
        masks = {k: v.reduce(SS) for k, v in masks.items()}
        # ring: brief brighter flash while it expands, settling at ring_opacity
        a_ring = min(1.0, float(C.get('ring_opacity', 0.55)) * _sstep(u_ring / 0.35) * (1 + 0.7 * (1 - _sstep(u_ring))))
        alphas = {'line': float(C.get('line_opacity', 0.9)), 'ring': a_ring, 'dot': _sstep(u_dot),
                  'edge': edge_a * _sstep(u_dot)}
        out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        if it['shadow']:
            sm = Image.new('L', (w, h), 0)
            for k, mk in masks.items():
                sm = Image.composite(Image.new('L', (w, h), 255), sm, mk.point(lambda v, a=alphas[k]: round(v * a)))
            out = Image.alpha_composite(out, _soft(sm, sig_l, float(lsh.get('opacity', 0.6)), it['shadow'], (0, dy_l)))
        for k in ('line', 'ring', 'edge', 'dot'):
            if alphas[k] > 0 and (k != 'edge' or it['shadow']):
                lay = Image.new('RGBA', (w, h), (rgb(it['shadow']) if k == 'edge' else it['col'][k]) + (0,))
                lay.putalpha(masks[k].point(lambda v, a=alphas[k]: round(v * a)))
                out = Image.alpha_composite(out, lay)
        return out

    last_key, last_bytes, full = None, None, {}
    for k in range(n):
        t = k / fps
        g = 1.0 - _sstep((t - (dur - fo)) / fo) if fo > 0 else 1.0
        state = []
        for it in group['items']:
            tt = t - float(it['t0'])
            state.append((round(_clamp01(tt / dot_s), 3) if tt >= 0 else 0.0,
                          round(_clamp01(tt / ring_s), 3) if tt >= 0 else 0.0,
                          round(_ease_out((tt - l_delay) / draw_s), 3) if tt > l_delay else 0.0,
                          round(_sstep((tt - l_delay - t_delay) / t_fade), 3) if tt > l_delay + t_delay else 0.0))
        key = (tuple(state), round(g, 3))
        f = out_dir / f'f_{k:04d}.png'
        if key != last_key:
            frame = Image.new('RGBA', (ux1 - ux0, uy1 - uy0), (0, 0, 0, 0))
            for it, (u_dot, u_ring, u_line, u_text) in zip(group['items'], state):
                if u_dot <= 0:
                    continue
                if (u_dot, u_ring, u_line) == (1.0, 1.0, 1.0):
                    if it['id'] not in full:
                        full[it['id']] = line_layer(it, 1.0, 1.0, 1.0)
                    ll = full[it['id']]
                else:
                    ll = line_layer(it, u_dot, u_ring, u_line)
                _paste_clipped(frame, _scale_alpha(ll, g), it['lbox'][0] - ux0, it['lbox'][1] - uy0)
                if u_text <= 0:
                    continue
                if u_text >= 0.999:
                    img, (x, y) = it['txt'], it['txt_xy']
                else:          # glide outward from `slide` px back towards the line (sub-pixel, bilinear)
                    back = slide * (1 - _ease_out(u_text))
                    dx = -it['side'] * back
                    img = it['glide'].transform(it['glide'].size, AFFINE, (1, 0, -dx, 0, 1, 0), resample=BILINEAR)
                    x, y = it['glide_xy']
                _paste_clipped(frame, _scale_alpha(img, u_text * g), x - ux0, y - uy0)
            buf = io.BytesIO()
            frame.save(buf, 'PNG', compress_level=1)
            last_key, last_bytes = key, buf.getvalue()
        f.write_bytes(last_bytes)
    geo = [dict(id=it['id'], A=[round(v, 1) for v in it['A']], L=[round(v, 1) for v in it['L']],
                pts=[[round(v, 1) for v in p] for p in it['pts']], text_box=[round(v, 1) for v in it['text_box']],
                ink=it['ink_used'], luma=it['luma'], scrim=it['scrim'], t0=round(float(it['t0']), 3))
           for it in group['items']]
    return dict(pattern=(out_dir / 'f_%04d.png').as_posix(), dir=str(out_dir), n=n, x=ux0, y=uy0, w=ux1 - ux0,
                h=uy1 - uy0, items=geo, warnings=warns)


# ----------------------------------------------------------------------------------------------- self-check

def _main(argv=None):
    ap = argparse.ArgumentParser(description='Show the fonts this machine resolves for each role; optionally '
                                             'render a caption specimen.')
    ap.add_argument('--project', default='.', help='project root (its fonts/ folder is searched first)')
    ap.add_argument('--size', default='1920x1080', help='frame size WxH for the specimen')
    ap.add_argument('--specimen', default=None, help='write a specimen PNG (caption + card) to this path')
    a = ap.parse_args(argv)
    utf8_stdout()
    project = Path(a.project).resolve()
    for role in FONT_ROLES:
        r = resolve_font('@' + role, project)
        print(f'@{role:<14} {describe_font(r) if r else "NOT FOUND"}')
    if a.specimen:
        W, H = (int(v) for v in a.size.lower().split('x'))
        st = Style({}, W, H, project)
        out = Path(a.specimen)
        bg = Image.new('RGB', (W, H), (40, 44, 52))
        card = out.with_name(out.stem + '_card.png')
        card_png(dict(style='title', mark='DEMO', zh='示例产品 · 字体检查', en='font specimen'), st, card)
        cap = caption_png(dict(zh='静｜运行时几乎听不见', en='quiet by design', emph=['听不见'], pos='lower_left'),
                          st, out.with_name(out.stem + '_cap.png'))
        frame = bg.convert('RGBA')
        frame.alpha_composite(Image.open(cap['png']), (max(0, cap['x']), max(0, cap['y'])))
        frame.convert('RGB').save(out)
        print('specimen', out, 'and', card)


if __name__ == '__main__':
    _main()
