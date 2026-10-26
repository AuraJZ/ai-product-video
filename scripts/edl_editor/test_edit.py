# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.1",
#     "numpy>=1.24",
#     "imageio-ffmpeg>=0.5.1",
# ]
# ///
"""Offline tests for edit.py planning (--dry, nothing rendered): frame size from the EDL, the orientation warning,
the feed safe box, caption roles that are not reading text, the one-frame gap tolerance and the card wording lint.
Uses small synthetic clips (ffmpeg testsrc2) and the fonts this machine has.

uv run test_edit.py
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edit as E  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()


def clip(path, w, h, seconds=5):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    f'testsrc2=size={w}x{h}:rate=24:duration={seconds}', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


def project(tmp, edl_extra=None, style=None, captions=None, cards=None):
    p = Path(tmp)
    for i in (1, 2, 3):
        if not (p / 'footage' / f'S0{i}.mp4').exists():
            clip(p / 'footage' / f'S0{i}.mp4', 270, 480)                # vertical sources
    edl = {'fps': 24, 'captions_file': 'edit/captions.json',
           'items': [{'type': 'clip', 'id': f'S0{i}', 'src': f'footage/S0{i}.mp4', 'in': 0.0, 'out': 4.0}
                     for i in (1, 2, 3)] + [{'type': 'card', 'id': 'end', 'dur': 3.0}]}
    edl.update(edl_extra or {})
    cap = {'style': dict({'min_gap_s': 0.6}, **(style or {})),
           'cards': cards or [{'id': 'end', 'style': 'end', 'mark': 'DEMO', 'zh': '一句收尾的话',
                               'note': '画面由 AI 生成 · 部分功能为视觉示意'}],
           'captions': captions or []}
    (p / 'edit').mkdir(exist_ok=True)
    (p / 'edit' / 'edl.json').write_text(json.dumps(edl, ensure_ascii=False), encoding='utf-8')
    (p / 'edit' / 'captions.json').write_text(json.dumps(cap, ensure_ascii=False), encoding='utf-8')
    return p


def dry(p, *extra):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        tl = E.main(['--project', str(p), '--edl', 'edit/edl.json', '--dry', *extra])
    return tl, out.getvalue()


def test_frame_size_from_edl_and_orientation_warning():
    with tempfile.TemporaryDirectory() as tmp:
        p = project(tmp)
        tl, out = dry(p)                                        # no size anywhere: the old landscape default ...
        assert tl['size'] == [854, 480], tl['size']
        assert any('3 of 3 sources are portrait' in w for w in tl['warnings']), tl['warnings']   # ... is flagged
        p = project(tmp, {'aspect': '9:16'})
        tl, _ = dry(p, '--res', '480')
        assert tl['size'] == [480, 854] and not any('sources are' in w for w in tl['warnings']), tl
        p = project(tmp, {'size': [1080, 1920]})
        tl, out = dry(p)
        assert tl['size'] == [1080, 1920] and '(portrait, EDL size)' in out, out
        tl, _ = dry(p, '--res', '720')
        assert tl['size'] == [720, 1280], tl['size']
        tl, out = dry(p, '--size', '1920x1080')                # asked for on purpose: a note, not a warning
        assert tl['size'] == [1920, 1080] and not any('sources are' in w for w in tl['warnings'])
        assert 'NOTE 3 of 3 sources are portrait' in out, out


def test_feed_safe_box():
    caps = [{'id': 'c1', 'anchor': 'S01', 'offset': 0.5, 'dur': 2.5, 'zh': '轻｜单手就能拿起', 'en': 'One hand',
             'pos': 'lower_left'},
            {'id': 'c2', 'anchor': 'S02', 'offset': 0.5, 'dur': 2.5, 'zh': '到点提醒', 'pos': 'upper_right'}]
    with tempfile.TemporaryDirectory() as tmp:
        p = project(tmp, {'size': [1080, 1920]}, style={'portrait': {'safe_box': [65, 288, 888, 1248]}}, captions=caps)
        tl, _ = dry(p)                                          # the looser built-in portrait positions
        outside = tl['safe_box']['outside']
        assert any(o.startswith('caption c1') for o in outside), outside
        assert any(o.startswith('card end: small print') for o in outside), outside
        p = project(tmp, {'size': [1080, 1920]}, style={'preset': 'feed_safe'}, captions=caps)
        tl, _ = dry(p)
        assert tl['safe_box']['outside'] == [] and tl['safe_box']['checked'] == 3, tl['safe_box']
        tl, _ = dry(p, '--size', '1920x1080')                   # feed_safe only touches vertical frames
        assert tl['safe_box'] is None


def test_label_note_roles_and_gap_tolerance():
    caps = [{'id': 'ai_label_open', 'anchor': 'S01', 'offset': 0.0, 'dur': 2.2, 'role': 'label',
             'zh': '本片含 AI 生成内容', 'emph': ['本片含 AI 生成内容'], 'pos': 'upper_left'},
            {'id': 'c1', 'anchor': 'S01', 'offset': 0.7, 'dur': 2.8, 'zh': '一句字幕', 'pos': 'lower_left'},
            {'id': 'c2', 'anchor': 'S02', 'offset': 0.1, 'dur': 2.0, 'zh': '第二句字幕', 'pos': 'lower_left'},
            {'id': 'end_note', 'anchor': 'S03', 'offset': 1.0, 'dur': 3.0, 'preset': 'note', 'role': 'note',
             'zh': '画面由 AI 生成 · 部分功能为视觉示意', 'en': 'AI-generated footage · some features illustrative',
             'pos': 'lower_center'},
            {'id': 'c3', 'anchor': 'S03', 'offset': 0.5, 'dur': 2.0, 'zh': '第三句字幕', 'pos': 'upper_left'}]
    with tempfile.TemporaryDirectory() as tmp:
        p = project(tmp, {'size': [1080, 1920]}, style={'preset': 'feed_safe'}, captions=caps)
        tl, _ = dry(p)
        gaps = [w for w in tl['warnings'] if 'apart' in w]
        # c1 ends at 3.5 s, c2 starts at 4.1 s: 0.6 s nominal, 0.58 s after rounding to frames -> no warning;
        # the label overlaps c1 and the note overlaps c3, but neither is reading text
        assert gaps == [], gaps
        assert not any('longer than' in w for w in tl['warnings']), tl['warnings']
        roles = {c['id']: c.get('reading', True) for c in tl['captions']}
        assert roles == {'ai_label_open': False, 'c1': True, 'c2': True, 'end_note': False, 'c3': True}, roles
        caps[2]['offset'] = 0.0                                  # a real 0.5 s gap is still reported
        p = project(tmp, {'size': [1080, 1920]}, style={'preset': 'feed_safe'}, captions=caps)
        tl, _ = dry(p)
        assert any('c1 and c2' in w for w in tl['warnings']), tl['warnings']


def test_card_text_is_linted():
    cards = [{'id': 'end', 'style': 'end', 'mark': 'DEMO', 'zh': '最好的磨豆机', 'note': '静音设计'}]
    with tempfile.TemporaryDirectory() as tmp:
        p = project(tmp, {'size': [1080, 1920], 'lint': {'banned_extra': ['静音']}}, cards=cards)
        tl, _ = dry(p)
        hits = [w for w in tl['warnings'] if w.startswith('card end')]
        assert hits and '最好' in hits[0] and '静音' in hits[0], tl['warnings']


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('PASS', name)
            except Exception as e:     # noqa: BLE001
                failed += 1
                print('FAIL', name, type(e).__name__, e)
    print(f'{failed} failed' if failed else 'all passed')
    sys.exit(1 if failed else 0)
