# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "imageio-ffmpeg>=0.5.1",
# ]
# ///
"""Build a throwaway demo project for edit.py: synthetic footage (ffmpeg test patterns, no real product) and the
matching edl.json / captions.json / callouts.json / ledgers, exercising every feature once.

    uv run make_demo.py DEMO_DIR [--force]
    uv run edit.py --project DEMO_DIR --dry
    uv run edit.py --project DEMO_DIR                    # 854x480, renders/edl_854x480.mp4
    uv run edit.py --project DEMO_DIR --size 1080x1920   # vertical cut from the same files

What the demo contains (18.9 s at 24 fps):
  fade in from black -> title card -> dissolve -> clip A (testsrc2) with a Chinese caption + channel glyph
  -> flare -> clip B (SMPTE bars, in-trimmed) with an English caption (preset "latin")
  -> bridge B_B_C (not approved, so its fallback whip is used) -> clip C (a grey "product" on a moving gradient,
     found through approved_drafts.json, held 4 s) with a 3-item callout group; in a vertical cut clip C uses
     "fit": "blur" via its "portrait" override -> dissolve -> end card with a note -> fade out.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS = 1920, 1080, 24

EDL = {
    '_doc': 'Demo EDL written by make_demo.py. Render: uv run edit.py --project <this folder> [--size 1080x1920]',
    'fps': FPS,
    'label': '演示素材 · 合成画面',
    'captions_file': 'captions.json',
    'callouts_file': 'callouts.json',
    'approved_drafts_file': 'approved_drafts.json',
    'approved_bridges_file': 'approved_bridges.json',
    'items': [
        {'type': 'transition', 'kind': 'dip_black', 'dur': 0.6},
        {'type': 'card', 'id': 'title', 'dur': 3.0, 'fade': [0.0, 0.0]},
        {'type': 'transition', 'kind': 'dissolve', 'dur': 0.8},
        {'type': 'clip', 'id': 'A', 'src': 'footage/A.mp4', 'in': 0.0, 'out': 4.0},
        {'type': 'transition', 'kind': 'flare', 'dur': 0.5},
        {'type': 'clip', 'id': 'B', 'src': 'footage/B.mp4', 'in': 0.5, 'out': 4.5},
        {'type': 'bridge', 'id': 'B_B_C', 'in': 0.0, 'out': 2.0,
         'fallback': {'kind': 'whip', 'dur': 0.4, 'dir': 'left'}},
        {'type': 'clip', 'id': 'C', 'in': 0.0, 'out': 3.0, 'hold_end': 4.0, 'callouts': 'C',
         'portrait': {'fit': 'blur'}},
        {'type': 'transition', 'kind': 'dissolve', 'dur': 0.8},
        {'type': 'card', 'id': 'end', 'dur': 3.0, 'fade': [0.6, 0.0]},
        {'type': 'transition', 'kind': 'dip_black', 'dur': 1.0},
    ],
}

CAPTIONS = {
    '_doc': 'Demo captions written by make_demo.py: "zh" = main line, "en" = small secondary line in caps.',
    'offset_basis': 'trimmed',
    'style': {'cards': {'channel_chars': '快'}},
    'cards': [
        {'id': 'title', 'style': 'title', 'mark': 'DEMO', 'zh': '示例产品 · 剪辑演示', 'en': 'EDL editor demo'},
        {'id': 'end', 'style': 'end', 'mark': 'DEMO', 'zh': '每一帧都可以复现', 'en': 'Made from an EDL',
         'note': '画面为合成测试图，仅用于演示\nSynthetic test footage, for demonstration only'},
    ],
    'captions': [
        {'id': 'A_setup', 'anchor': 'A', 'offset': 0.6, 'dur': 2.8, 'zh': '快｜三步完成安装',
         'en': 'Set up in three steps', 'emph': ['三步'], 'pos': 'lower_left', 'ink': 'light'},
        {'id': 'B_carry', 'anchor': 'B', 'offset': 0.4, 'dur': 2.8, 'preset': 'latin',
         'zh': 'Built for everyday carry', 'en': 'Demo product', 'emph': ['everyday carry'], 'en_emph': ['Demo'],
         'pos': 'lower_right', 'ink': 'light'},
    ],
}

CALLOUTS = {
    '_doc': 'Demo callouts written by make_demo.py. anchor_xy / label_xy are 0-1 on the source frame; "at" and '
            '"stagger" run on the clip clock (source seconds, continuing into hold_end).',
    'C': {
        'at': 2.5, 'stagger': 0.35, 'fade_out': 0.5,
        'items': [
            {'id': 'lid', 'order': 1, 'anchor_xy': [0.56, 0.29], 'label_xy': [0.68, 0.22],
             'zh': '顶盖', 'fn': '一体成型', 'en': 'Top cover'},
            {'id': 'button', 'order': 2, 'anchor_xy': [0.50, 0.475], 'label_xy': [0.68, 0.475],
             'zh': '按键', 'fn': '单键操作', 'en': 'One button'},
            {'id': 'base', 'order': 3, 'anchor_xy': [0.43, 0.77], 'label_xy': [0.30, 0.70],
             'zh': '底座', 'fn': '硅胶脚垫', 'en': 'Silicone feet'},
        ],
    },
}

DRAFTS = {'_doc': 'clip id -> approved take: a task id (resolved through the EDL ledger_pattern, default '
                  'gen/{id}/{task} + extension), a media path, or {"task": ..., "src": ...}',
          'C': 'take_03'}
BRIDGES = {'_doc': 'bridge id -> approved take (same forms as approved_drafts.json). B_B_C is not approved yet, '
                   'so the EDL uses its fallback transition.'}


def box(x0, y0, x1, y1, color):
    """drawbox over normalised coordinates."""
    return (f'drawbox=x={round(x0 * W)}:y={round(y0 * H)}:w={round((x1 - x0) * W)}:h={round((y1 - y0) * H)}:'
            f'color=0x{color}@1:t=fill')


CLIPS = {
    'footage/A.mp4': (f'testsrc2=size={W}x{H}:rate={FPS}:duration=5', None),
    'footage/B.mp4': (f'smptebars=size={W}x{H}:rate={FPS}:duration=5', None),
    # a plain "product" (body, lid band, button, base) on a slowly moving dark gradient
    'gen/C/take_03.mp4': (f'gradients=size={W}x{H}:rate={FPS}:duration=4:speed=0.004:nb_colors=2:'
                          f'c0=0x1c2229:c1=0x3b4652',
                          ','.join([box(0.40, 0.25, 0.60, 0.80, 'D8D4CC'), box(0.40, 0.25, 0.60, 0.33, '8A8580'),
                                    box(0.48, 0.45, 0.52, 0.50, '3A3632'), box(0.40, 0.74, 0.60, 0.80, '55504A')])),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build a throwaway demo project for edit.py')
    ap.add_argument('folder', help='demo project folder (created; must be empty unless --force)')
    ap.add_argument('--force', action='store_true', help='write into a non-empty folder (overwrites demo files)')
    a = ap.parse_args(argv)
    root = Path(a.folder).resolve()
    if root.exists() and any(root.iterdir()) and not a.force:
        raise SystemExit(f'{root} is not empty; pass --force to write the demo files into it anyway')
    root.mkdir(parents=True, exist_ok=True)
    for rel, (src, vf) in CLIPS.items():
        out = root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [FF, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', src]
        cmd += ['-vf', vf] if vf else []
        cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20', '-pix_fmt', 'yuv420p', str(out)]
        subprocess.run(cmd, check=True)
        print('clip', out.relative_to(root).as_posix())
    for name, data in (('edl.json', EDL), ('captions.json', CAPTIONS), ('callouts.json', CALLOUTS),
                       ('approved_drafts.json', DRAFTS), ('approved_bridges.json', BRIDGES)):
        (root / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('json', name)
    here = Path(__file__).resolve().parent / 'edit.py'
    print(f'\nDemo project ready: {root}\nNext:\n  uv run "{here}" --project "{root}" --dry\n'
          f'  uv run "{here}" --project "{root}"\n  uv run "{here}" --project "{root}" --size 1080x1920')


if __name__ == '__main__':
    sys.exit(main())
