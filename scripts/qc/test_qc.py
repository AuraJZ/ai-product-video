# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=1.24",
#     "pillow>=10.1",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Tests for strip.py and contact_sheet.py on a synthetic 6 s clip (a slow pan over one texture, a hard cut at
2.5 s to another texture, a frozen second from 4.0 to 5.0 s, then the pan again), and for normalise_keyframes.py and
lint_prompts.py on small synthetic projects.

uv run test_qc.py
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contact_sheet as C  # noqa: E402
import lint_prompts as LP  # noqa: E402
import normalise_keyframes as NK  # noqa: E402
import strip as S  # noqa: E402

FPS, W, H = 24, 640, 360


def texture(seed, tint, size=(1400, 360)):
    rng = np.random.default_rng(seed)
    base = Image.fromarray((rng.random((size[1] // 8, size[0] // 8)) * 255).astype(np.uint8)).resize(size, Image.BICUBIC)
    a = np.asarray(base.filter(ImageFilter.GaussianBlur(2)), np.float32)[..., None] / 255
    return (a * np.array(tint, np.float32)).clip(0, 255).astype(np.uint8)


def make_clip(path):
    a, b = texture(1, (230, 200, 160)), texture(2, (90, 140, 220))
    frames = []
    for i in range(6 * FPS):
        if i < 60:                      # 0-2.5 s: scene A, pan 3 px/frame
            f = a[:, i * 3:i * 3 + W]
        elif i < 96:                    # 2.5-4.0 s: hard cut to scene B, still panning
            f = b[:, (i - 60) * 3:(i - 60) * 3 + W]
        elif i < 120:                   # 4.0-5.0 s: frozen on the frame at 4.0 s
            f = b[:, 36 * 3:36 * 3 + W]
        else:                           # 5.0-6.0 s: the pan resumes
            f = b[:, (i - 83) * 3:(i - 83) * 3 + W]
        frames.append(np.ascontiguousarray(f))
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt',
           'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
           str(path)]
    r = subprocess.run(cmd, input=b''.join(f.tobytes() for f in frames), capture_output=True)
    assert r.returncode == 0, r.stderr


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a)
    return res, buf.getvalue()


def test_strip_flags_cut_and_freeze():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / 'gen').mkdir()
        make_clip(p / 'gen' / 'take1.mp4')
        res, out = quiet(S.main, ['gen/take1.mp4', '--project', str(p)])
        r = res[0]
        kinds = [(f['type'], f['t']) for f in r['flags']]
        cuts = [f for f in r['flags'] if f['type'] == 'cut']
        freezes = [f for f in r['flags'] if f['type'] == 'freeze']
        assert len(cuts) == 1 and abs(cuts[0]['t'] - 2.5) < 0.05, kinds
        assert len(freezes) == 1 and abs(freezes[0]['t'] - 4.0) < 0.05 and 0.9 <= freezes[0]['seconds'] <= 1.0, kinds
        assert len(r['flags']) == 2, kinds
        assert r['frames'] == 144 and len(r['per_frame']) == 144
        assert 24 <= len(r['samples']) <= 25
        for suffix in ('_strip.png', '_curves.png', '_strip.json'):
            assert (p / 'qc' / f'take1{suffix}').is_file()
        js = json.loads((p / 'qc' / 'take1_strip.json').read_text(encoding='utf-8'))
        assert js['flags'] == r['flags']
        with Image.open(p / 'qc' / 'take1_strip.png') as im:
            assert im.width > 8 * 320
        print(f'    {kinds}; motion median {r["motion_median"]}, cut motion {cuts[0]["motion"]} '
              f'(x{cuts[0]["ratio"]})')

        res, _ = quiet(S.main, ['gen/take1.mp4', '--project', str(p), '--start', '2', '--end', '3'])
        cuts = [f for f in res[0]['flags'] if f['type'] == 'cut']
        assert len(cuts) == 1 and abs(cuts[0]['t'] - 2.5) < 0.05, res[0]['flags']     # times stay absolute
        assert (p / 'qc' / 'take1_2-3_strip.png').is_file()

        try:
            quiet(S.main, ['gen', '--project', str(p), '--fail-on-flags'])
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError('--fail-on-flags did not fail')


def test_strip_clean_clip_has_no_flags():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        a = texture(3, (200, 200, 200))
        frames = b''.join(np.ascontiguousarray(a[:, i * 2:i * 2 + W]).tobytes() for i in range(72))
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
               '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-crf', '18',
               '-pix_fmt', 'yuv420p', str(p / 'clean.mp4')]
        assert subprocess.run(cmd, input=frames, capture_output=True).returncode == 0
        res, _ = quiet(S.main, ['clean.mp4', '--project', str(p)])
        assert res[0]['flags'] == [], res[0]['flags']


def test_contact_sheet():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / 'kf').mkdir()
        Image.new('RGB', (1600, 900), (200, 60, 60)).save(p / 'kf' / 'a.png')
        Image.new('RGB', (900, 1600), (60, 200, 60)).save(p / 'kf' / 'b.jpg')
        make_clip(p / 'clip.mp4')
        out, txt = quiet(C.main, ['qc/sheet.png', 'kf', 'clip.mp4@last', 'clip.mp4@1.0', 'clip.mp4@mid',
                                  '--project', str(p), '--cols', '3', '--width', '200', '--title', '对比 test'])
        with Image.open(out) as im:
            assert im.width == 12 + 3 * (200 + 12), im.size
        assert 'SHEET qc/sheet.png  5 images' in txt, txt
        out, txt = quiet(C.main, ['qc/glob.jpg', 'kf/*.png', '--project', str(p), '--label', 'A', '--label', 'B'])
        with Image.open(out) as im:
            assert 'WARN' in txt and im.width == 12 + 480 + 12, im.size      # the glob matches a.png only


def test_normalise_keyframes():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        raw = p / 'keyframes' / 'raw'
        raw.mkdir(parents=True)
        Image.new('RGB', (1152, 2048), (200, 60, 60)).save(raw / 'S01-K00.png')       # vertical frame, larger
        Image.new('RGB', (1024, 1536), (60, 200, 60)).save(raw / 'S01-K04.jpg')       # 2:3 jpg: crop + upscale
        Image.new('RGB', (2400, 1000), (60, 60, 200)).save(raw / 'M1.png')            # landscape reference sheet
        Image.new('RGB', (1600, 900), (90, 90, 90)).save(raw / 'P1.webp')             # master by keyframes.json
        Image.new('RGB', (1080, 1920), (9, 9, 9)).save(raw / 'S02-K00.png')
        Image.new('RGB', (1080, 1920), (9, 9, 9)).save(raw / 'S02-K00.jpg')            # same id twice
        (p / 'keyframes' / 'keyframes.json').write_text(json.dumps(
            [{'id': 'P1', 'role': 'master', 'save_as': 'keyframes/raw/P1.webp'},
             {'id': 'S01-K00', 'role': 'first'}]), encoding='utf-8')
        rep, out = quiet(NK.main, ['--project', str(p), '--size', '1080x1920'])
        sizes = {d['id']: tuple(d['to']) for d in rep['done']}
        assert sizes == {'S01-K00': (1080, 1920), 'S01-K04': (1080, 1920), 'M1': (1920, 800), 'P1': (1600, 900)}, sizes
        assert any(w.startswith('S01-K04: UPSCALED') for w in rep['warnings']), rep['warnings']
        assert any(w.startswith('S02-K00: DUPLICATE') for w in rep['warnings']), rep['warnings']
        assert not any(w.startswith(('M1', 'P1', 'S01-K00')) for w in rep['warnings']), rep['warnings']
        with Image.open(p / 'keyframes' / 'norm' / 'M1.png') as im:
            assert im.size == (1920, 800)                    # never cropped to a 9:16 strip
        assert not (p / 'keyframes' / 'norm' / 'S02-K00.png').exists()
        rep, _ = quiet(NK.main, ['--project', str(p), '--size', '1920x1080', '--only', 'S01-K00', '--dry'])
        assert any('CROPPED' in w for w in rep['warnings']), rep['warnings']          # vertical frame, 16:9 film


def test_lint_prompts():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / 'shots' / 'prompts').mkdir(parents=True)
        (p / 'shots' / 'prompts' / 'S01.txt').write_text(
            '镜头缓慢推近。\n指示灯慢慢熄灭，产品本身不发光。\n结束时镜头停在门口。', encoding='utf-8')
        (p / 'words.json').write_text(json.dumps({'not this product': ['喷雾'], '_note': 'x'}), encoding='utf-8')
        (p / 'shots' / 'prompts' / 'S02.txt').write_text('杯口有喷雾。', encoding='utf-8')
        hits, out = quiet(LP.main, ['--project', str(p), '--families', 'words.json'])
        got = sorted((h['file'].split('/')[-1], h['line'], h['family']) for h in hits)
        assert got == [('S01.txt', 2, 'makes a light source'), ('S01.txt', 2, 'switches a steady light off'),
                       ('S01.txt', 3, 'invents a camera move or an exit'), ('S02.txt', 1, 'not this product')], got
        assert 'SCANNED 2 prompt file(s)' in out, out
        try:                                                  # a wrong project must not look clean
            quiet(LP.main, ['--project', str(p / 'nowhere')])
        except SystemExit as e:
            assert e.code == 1
        else:
            raise AssertionError('scanning zero files did not fail')


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
