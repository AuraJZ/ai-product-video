# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=1.24",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Tests for mix.py on synthetic media: a sine-chord music stem (16-bit mono 44.1 kHz, shorter than the picture),
a click SFX stem (32-bit float stereo 48 kHz, longer than the picture) and a 10 s test-pattern video.
Checks the ramps, the before/after window numbers, the loudness of the result (ffmpeg ebur128) and the stream copy.

uv run test_mix.py
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mix as M  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
TICKS = (2.0, 2.4, 2.8, 3.2)


def ff(*args):
    r = subprocess.run([FF, '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode('utf-8', 'replace')


def write_wav(path, x, sr, codec):
    raw = path.with_suffix('.f32')
    x.astype('<f4').tofile(raw)
    ff('-f', 'f32le', '-ar', sr, '-ac', x.shape[1], '-i', raw, '-c:a', codec, path)
    raw.unlink()


def make_media(d):
    d = Path(d)
    ff('-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24', '-t', 10, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
       d / 'video.mp4')
    sr = 44100
    t = np.arange(int(9.6 * sr)) / sr
    music = sum(0.12 * np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63))[:, None]
    write_wav(d / 'music.wav', music, sr, 'pcm_s16le')
    sr = 48000
    sfx = np.zeros((int(10.4 * sr), 2))
    n = int(0.025 * sr)
    burst = 0.5 * np.sin(2 * np.pi * 2000 * np.arange(n) / sr) * np.hanning(n)
    for tt in TICKS + (7.0,):
        i = int(tt * sr)
        sfx[i:i + n] += burst[:, None]
    write_wav(d / 'sfx.wav', sfx, sr, 'pcm_f32le')
    windows = [{'start': 1.8, 'end': 3.4, 'target': 'music', 'gain_db': -6, 'label': 'ticks duck'},
               {'start': 1.8, 'end': 3.4, 'target': 'sfx', 'gain_db': 6, 'label': 'ticks lift'},
               {'start': 6.8, 'end': 7.4, 'target': 'music', 'gain_db': 0, 'label': 'measure only'}]
    (d / 'windows.json').write_text(json.dumps(windows), encoding='utf-8')
    return d


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a)
    return res, buf.getvalue()


def test_ramp_shape():
    sr = 1000
    w = [{'start': 1.0, 'end': 2.0, 'target': 'music', 'gain_db': -6.0, 'ramp': None, 'label': 'x'}]
    env = M.envelope_db(3000, sr, w, 'music', -3.0, 0.5)
    assert abs(env[400] + 3) < 1e-9          # before the ramp: only the global trim
    assert abs(env[750] + 6) < 1e-6          # half-way up the raised cosine: -3 -3
    assert abs(env[1500] + 9) < 1e-9         # inside: -3 -6
    assert abs(env[2250] + 6) < 1e-6
    assert abs(env[2600] + 3) < 1e-9
    assert np.all(M.envelope_db(3000, sr, w, 'sfx', 0.0, 0.5) == 0)


def test_window_validation():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'w.json'
        p.write_text(json.dumps([{'start': 2, 'end': 1, 'target': 'vo', 'gain_db': 3}]), encoding='utf-8')
        try:
            M.load_windows(p)
        except SystemExit as e:
            assert 'before end' in str(e) and 'target' in str(e)
        else:
            raise AssertionError('bad windows accepted')


def test_mix_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_media(tmp)
        argv = ['--project', str(d), '--video', 'video.mp4', '--music', 'music.wav', '--sfx', 'sfx.wav',
                '--windows', 'windows.json', '--music-gain', '-3']
        rep, out = quiet(M.main, argv + ['--measure-only'])
        assert 'padded with silence' in out and 'cut' in out, out
        ticks, meas = rep['windows']
        assert {g['target'] for g in ticks['gains']} == {'music', 'sfx'}
        hb = [h['diff_db'] for h in ticks['before']['hits']]
        ha = [h['diff_db'] for h in ticks['after']['hits']]
        assert len(ha) == len(TICKS), ticks['after']['hits']
        assert all(abs((y - x) - 15.0) < 0.3 for x, y in zip(hb, ha)), (hb, ha)    # +6 lift, -6 duck, -3 trim
        assert abs(meas['after']['rms_diff_db'] - meas['before']['rms_diff_db'] - 3.0) < 0.2
        assert abs(meas['after']['peak60']['t'] - 7.0) < 0.07

        rep, out = quiet(M.main, argv + ['--out', 'out/mix.mp4', '--report', 'out/report.json', '--save-stems', 'out'])
        final = rep['loudness']['final']
        assert abs(final['integrated_lufs'] + 16.0) <= 0.5, final
        assert final['true_peak_dbtp'] <= -1.0, final
        assert not rep['warnings'], rep['warnings']
        dur, ch, has_v = M.stream_info(d / 'out' / 'mix.mp4')
        assert has_v and ch == 2 and abs(dur - 10.0) < 0.1, (dur, ch, has_v)
        probe = subprocess.run([FF, '-hide_banner', '-i', str(d / 'out' / 'mix.mp4')], capture_output=True).stderr
        assert b'h264' in probe and b'aac' in probe
        assert (d / 'out' / 'report.json').is_file() and (d / 'out' / 'music_balanced.wav').is_file()

        rep, out = quiet(M.main, ['--project', str(d), '--music', 'music.wav', '--sfx', 'sfx.wav',
                                  '--out', 'out/master.wav'])
        assert abs(rep['loudness']['final']['integrated_lufs'] + 16.0) <= 0.5
        print(f'    final {final}')


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
