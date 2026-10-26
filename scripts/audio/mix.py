# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=1.24",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Final mix: music stem + SFX stem -> timed ducks / lifts -> limiter -> two-pass loudness normalisation -> AAC,
muxed onto the picture (the video stream is copied, never re-encoded).

  uv run mix.py --video edit/cut.mp4 --music sound/music.wav --sfx sound/sfx.wav --windows sound/windows.json \
      --out delivery/cut_mix.mp4 --music-gain -3 --report sound/mix_report.json
  uv run mix.py --music sound/music.wav --sfx sound/sfx.wav --windows sound/windows.json --measure-only
  uv run mix.py --music sound/music.wav --sfx sound/sfx.wav --out sound/master.wav      # audio only, no picture

Stems: any WAV (16/24/32-bit integer or float samples, mono or stereo, any rate; anything ffmpeg reads works), resampled to --sr.
Put a voice-over into the SFX stem (or mix it there first): the SFX stem is "the foreground the music must not cover".

windows.json, a list (or {"windows": [...]}) of
  {"start": 12.0, "end": 14.5, "target": "music", "gain_db": -5, "label": "callouts"}
  target music = duck the music under a cue; target sfx = lift the cue itself; gain_db 0 = measure only.
  Inside [start, end] the gain is full; it ramps in before start and out after end over --ramp seconds (raised
  cosine; a window may carry its own "ramp"). Gains on the same stem add up in dB.

The measurement compares the SFX stem with the music stem inside each window, before (raw stems) and after (all
gains): RMS over the window, the loudest 60 ms block, and every SFX hit (local 60 ms peaks), in dB. Short ticks
can be fine on average and still buried hit by hit, which is why the per-hit numbers exist.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg
import numpy as np

FF = imageio_ffmpeg.get_ffmpeg_exe()       # bundled binary; IMAGEIO_FFMPEG_EXE points it at another ffmpeg
BLOCK = 0.060                               # seconds; short enough to see a single tick
EPS = 1e-9


def run(args, **kw):
    return subprocess.run([FF, '-hide_banner', '-nostdin', *map(str, args)], capture_output=True, **kw)


def db(x):
    return 20 * math.log10(max(float(x), EPS))


# ---------------------------------------------------------------------------------------------------------------------
# Reading


def stream_info(path):
    """(duration s or None, audio channels or None, has video) from ffmpeg's header dump."""
    err = run(['-i', path]).stderr.decode('utf-8', 'replace')
    if 'No such file' in err or 'Invalid data' in err:
        raise SystemExit(f'{path}: ffmpeg cannot read it')
    m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    ch = None
    a = re.search(r'Stream #[^\n]*Audio:[^\n]*?(\d+) Hz,\s*([^,\n]+)', err)
    if a:
        lay = a[2].strip()
        n = re.match(r'(\d+) channels', lay)
        ch = 1 if lay.startswith('mono') else 2 if lay.startswith('stereo') else int(n[1]) if n else 6
    return dur, ch, bool(re.search(r'Stream #[^\n]*Video:', err))


def read_audio(path, sr):
    """Any audio file -> float32 array (samples, 2) at sr. Mono is copied to both sides; more channels are downmixed."""
    _, ch, _ = stream_info(path)
    if not ch:
        raise SystemExit(f'{path}: no audio stream')
    args = ['-i', path, '-vn', '-map', '0:a:0', '-f', 'f32le', '-acodec', 'pcm_f32le', '-ar', sr]
    if ch > 2:
        args += ['-ac', 2]
        print(f'  {Path(path).name}: {ch} channels, downmixed to stereo')
    r = run(args + ['-'])
    if r.returncode:
        raise SystemExit(f'{path}: decode failed\n{r.stderr.decode("utf-8", "replace")[-800:]}')
    x = np.frombuffer(r.stdout, np.float32).reshape(-1, min(ch, 2))
    return np.repeat(x, 2, axis=1) if x.shape[1] == 1 else x.copy()


def fit_length(x, n):
    if len(x) >= n:
        return x[:n]
    return np.vstack([x, np.zeros((n - len(x), 2), np.float32)])


# ---------------------------------------------------------------------------------------------------------------------
# Windows and gain envelopes


def load_windows(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    data = data.get('windows', data) if isinstance(data, dict) else data
    if not isinstance(data, list):
        raise SystemExit(f'{path}: expected a list of windows or {{"windows": [...]}}')
    out, bad = [], []
    for i, w in enumerate(data, 1):
        try:
            s, e, g = float(w['start']), float(w['end']), float(w.get('gain_db', 0))
            tgt = str(w.get('target', 'music'))
        except (KeyError, TypeError, ValueError):
            bad.append(f'window {i}: needs numeric start, end (and gain_db)')
            continue
        if not 0 <= s < e:
            bad.append(f'window {i}: start {s} must be >= 0 and before end {e}')
        if tgt not in ('music', 'sfx'):
            bad.append(f'window {i}: target {tgt!r} must be music or sfx')
        ramp = w.get('ramp')
        out.append({'start': s, 'end': e, 'target': tgt, 'gain_db': g, 'label': str(w.get('label', f'window {i}')),
                    'ramp': float(ramp) if ramp is not None else None})
    if bad:
        raise SystemExit('windows file problems:\n  ' + '\n  '.join(bad))
    return out


def envelope_db(n, sr, windows, target, base_db, ramp):
    """Gain in dB per sample: base + every window of this target, raised-cosine ramps outside [start, end]."""
    t = np.arange(n, dtype=np.float64) / sr
    env = np.full(n, base_db, dtype=np.float64)
    for w in windows:
        if w['target'] != target or not w['gain_db']:
            continue
        r = w['ramp'] if w['ramp'] is not None else ramp
        if r > 0:
            up = np.clip((t - (w['start'] - r)) / r, 0, 1)
            down = np.clip(((w['end'] + r) - t) / r, 0, 1)
            shape = np.minimum(up, down)
            shape = 0.5 - 0.5 * np.cos(np.pi * shape)
        else:
            shape = ((t >= w['start']) & (t <= w['end'])).astype(np.float64)
        env += w['gain_db'] * shape
    return env


def apply_db(x, env):
    return (x * (10 ** (env / 20)).astype(np.float32)[:, None]).astype(np.float32)


# ---------------------------------------------------------------------------------------------------------------------
# Measurement


def block_db(x, nb):
    k = len(x) // nb
    if k == 0:
        return np.array([])
    p = np.mean(x[:k * nb].astype(np.float64).reshape(k, nb, -1) ** 2, axis=(1, 2))
    return 10 * np.log10(np.maximum(p, EPS ** 2))


def hits(sfx_db, floor_rel=20.0, floor_abs=-70.0, min_gap=2, cap=64):
    """Indices of SFX hits: local maxima of the 60 ms block levels, within floor_rel dB of the loudest block."""
    if len(sfx_db) == 0:
        return []
    top = sfx_db.max()
    cand = [i for i in range(len(sfx_db))
            if sfx_db[i] >= top - floor_rel and sfx_db[i] > floor_abs
            and (i == 0 or sfx_db[i] >= sfx_db[i - 1]) and (i == len(sfx_db) - 1 or sfx_db[i] > sfx_db[i + 1])]
    keep = []
    for i in sorted(cand, key=lambda j: -sfx_db[j]):
        if all(abs(i - j) >= min_gap for j in keep):
            keep.append(i)
    return sorted(keep)[:cap]


def measure(music, sfx, sr, w):
    """SFX minus music inside one window: RMS, loudest 60 ms block, and each hit."""
    i0, i1 = int(round(w['start'] * sr)), int(round(w['end'] * sr))
    m, s = music[i0:i1], sfx[i0:i1]
    if len(m) == 0:
        return None
    rm, rs = db(np.sqrt(np.mean(m.astype(np.float64) ** 2))), db(np.sqrt(np.mean(s.astype(np.float64) ** 2)))
    nb = max(1, int(round(BLOCK * sr)))
    mdb, sdb = block_db(m, nb), block_db(s, nb)
    res = {'sfx_rms_db': round(rs, 1), 'music_rms_db': round(rm, 1), 'rms_diff_db': round(rs - rm, 1)}
    if len(sdb):
        j = int(np.argmax(sdb))
        res['peak60'] = {'t': round(w['start'] + j * BLOCK, 3), 'sfx_db': round(float(sdb[j]), 1),
                         'music_db': round(float(mdb[j]), 1), 'diff_db': round(float(sdb[j] - mdb[j]), 1)}
        hs = hits(sdb)
        res['hits'] = [{'t': round(w['start'] + i * BLOCK, 3), 'diff_db': round(float(sdb[i] - mdb[i]), 1)} for i in hs]
        if hs:
            d = [float(sdb[i] - mdb[i]) for i in hs]
            res['hits_diff_min_db'], res['hits_diff_median_db'] = round(min(d), 1), round(float(np.median(d)), 1)
    return res


def print_table(rows):
    print(f'\n{"window":<24} {"time s":>13}  {"gain dB":<18} {"RMS diff":>15}   {"loudest 60 ms":>15}   '
          f'{"hits min / median (n)":>24}')
    for r in rows:
        b, a = r['before'] or {}, r['after'] or {}

        def pair(k, sub=None):
            x = (b.get(k) or {}).get(sub) if sub else b.get(k)
            y = (a.get(k) or {}).get(sub) if sub else a.get(k)
            return f'{x:+.1f} -> {y:+.1f}' if x is not None and y is not None else '-'
        hm = (f'{a["hits_diff_min_db"]:+.1f} / {a["hits_diff_median_db"]:+.1f} ({len(a["hits"])})'
              if a.get('hits_diff_min_db') is not None else '-')
        gains = ', '.join(f'{g["target"]} {g["gain_db"]:+g}' for g in r['gains']) or 'measure'
        print(f'{r["label"][:24]:<24} {r["start"]:6.2f}-{r["end"]:6.2f}  {gains:<18} {pair("rms_diff_db"):>15}   '
              f'{pair("peak60", "diff_db"):>15}   {hm:>24}')
    print('dB = SFX minus music, before (raw stems) -> after (all gains). Negative = the cue sits under the music.')


# ---------------------------------------------------------------------------------------------------------------------
# Loudness


def loudnorm_measure(raw, sr, chain, target, tp, lra):
    r = run(['-f', 'f32le', '-ar', sr, '-ac', 2, '-i', raw, '-af',
             f'{chain}loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json', '-f', 'null', '-'])
    err = r.stderr.decode('utf-8', 'replace')
    found = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', err)
    if r.returncode or not found:
        raise SystemExit('loudness measurement failed\n' + err[-1200:])
    m = json.loads(found[-1])
    if 'inf' in str(m.get('input_i')):
        raise SystemExit('the mix is silent: check the stems and the gains')
    return m


def ebur128(path):
    r = run(['-i', path, '-map', '0:a:0', '-af', 'ebur128=peak=true', '-f', 'null', '-'])
    err = r.stderr.decode('utf-8', 'replace')
    tail = err[err.rfind('Summary:'):]

    def num(pat):
        m = re.search(pat, tail)
        return float(m[1]) if m and 'inf' not in m[1] else None
    return {'integrated_lufs': num(r'I:\s*(-?[\d.]+|-?inf)\s*LUFS'), 'lra_lu': num(r'LRA:\s*(-?[\d.]+)\s*LU'),
            'true_peak_dbtp': num(r'Peak:\s*(-?[\d.]+|-?inf)\s*dBFS')}


# ---------------------------------------------------------------------------------------------------------------------


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--project', default='.', help='project root; relative paths are resolved here (default: .)')
    ap.add_argument('--video', help='picture to put the mix on (its video stream is copied)')
    ap.add_argument('--music', help='music stem')
    ap.add_argument('--sfx', help='SFX (foreground) stem')
    ap.add_argument('--windows', help='windows JSON (see the top of this file)')
    ap.add_argument('--out', help='output: .mp4/.mov with --video; .wav/.m4a without')
    ap.add_argument('--music-gain', type=float, default=0.0, help='whole-film music trim, dB (start around -3 if the '
                    'music covers the cues)')
    ap.add_argument('--sfx-gain', type=float, default=0.0, help='whole-film SFX trim, dB')
    ap.add_argument('--ramp', type=float, default=0.6, help='window ramp, seconds (default 0.6)')
    ap.add_argument('--target', type=float, default=-16.0, help='integrated loudness, LUFS (default -16)')
    ap.add_argument('--tp', type=float, default=-1.5, help='true-peak ceiling, dBTP (default -1.5)')
    ap.add_argument('--lra', type=float, default=11.0, help='loudness range target, LU (default 11)')
    ap.add_argument('--limit-db', type=float, default=-1.0, help='limiter ceiling before normalisation, dBFS')
    ap.add_argument('--sr', type=int, default=48000, help='mix sample rate (default 48000)')
    ap.add_argument('--bitrate', default='256k', help='AAC bitrate (default 256k)')
    ap.add_argument('--report', help='write the measurement and loudness numbers to this JSON file')
    ap.add_argument('--measure-only', action='store_true', help='print the window measurements; render nothing')
    ap.add_argument('--save-stems', help='folder for the balanced stems (32-bit float WAV) as mixed')
    a = ap.parse_args(argv)

    proj = Path(a.project).resolve()
    P = (lambda s: (proj / s) if s else None)
    video, out = P(a.video), P(a.out)
    if not (a.music or a.sfx):
        ap.error('give --music and/or --sfx')
    if not a.measure_only and not out:
        ap.error('--out is required unless --measure-only')
    if out and video and out.resolve() == video.resolve():
        ap.error('--out must not overwrite --video')
    windows = load_windows(P(a.windows)) if a.windows else []

    sr = a.sr
    vdur = None
    if video:
        vdur, _, has_v = stream_info(video)
        if not has_v or not vdur:
            raise SystemExit(f'{a.video}: no video stream or unknown duration')
    stems = {}
    for name, path in (('music', a.music), ('sfx', a.sfx)):
        if path:
            stems[name] = read_audio(P(path), sr)
            print(f'{name:<5} {path}: {len(stems[name]) / sr:.2f} s')
    n = int(round(vdur * sr)) if vdur else max(len(x) for x in stems.values())
    for name in ('music', 'sfx'):
        if name not in stems:
            stems[name] = np.zeros((n, 2), np.float32)
            if any(w['target'] == name and w['gain_db'] for w in windows):
                print(f'WARN  windows target {name}, but there is no {name} stem')
        elif vdur and abs(len(stems[name]) - n) > 0.1 * sr:
            print(f'WARN  {name} stem is {len(stems[name]) / sr:.2f} s, the picture {vdur:.2f} s: '
                  f'{"padded with silence" if len(stems[name]) < n else "cut"} to the picture')
        stems[name] = fit_length(stems[name], n)
    late = [w['label'] for w in windows if w['start'] >= n / sr]
    if late:
        print(f'WARN  windows after the end ({n / sr:.2f} s): {", ".join(late)}')

    env_m = envelope_db(n, sr, windows, 'music', a.music_gain, a.ramp)
    env_s = envelope_db(n, sr, windows, 'sfx', a.sfx_gain, a.ramp)
    music, sfx = apply_db(stems['music'], env_m), apply_db(stems['sfx'], env_s)

    rows = []
    for w in windows:
        gains = [{'target': x['target'], 'gain_db': x['gain_db']} for x in windows
                 if x['gain_db'] and x['start'] == w['start'] and x['end'] == w['end']]
        rows.append({**w, 'gains': gains, 'before': measure(stems['music'], stems['sfx'], sr, w),
                     'after': measure(music, sfx, sr, w)})
    # one row per distinct span: a duck and a lift over the same span measure the same thing
    seen, table = set(), []
    for r in rows:
        if (r['start'], r['end']) not in seen:
            seen.add((r['start'], r['end']))
            table.append(r)
    if table:
        print_table(table)
    report = {'settings': {k: getattr(a, k) for k in ('music_gain', 'sfx_gain', 'ramp', 'target', 'tp', 'lra',
                                                      'limit_db', 'sr', 'bitrate')},
              'inputs': {k: getattr(a, k) for k in ('video', 'music', 'sfx', 'windows')},
              'length_s': round(n / sr, 3), 'windows': table}

    def write_report():
        if a.report:
            P(a.report).parent.mkdir(parents=True, exist_ok=True)
            P(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
            print('report', a.report)

    if a.measure_only:
        write_report()
        return report

    mix = music.astype(np.float64) + sfx.astype(np.float64)
    report['pre_limiter_peak_dbfs'] = round(db(np.max(np.abs(mix))), 2)
    limit = 10 ** (a.limit_db / 20)
    chain = f'alimiter=limit={limit:.4f}:attack=5:release=80:level=0,'
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / 'mix.f32'
        mix.astype('<f4').tofile(raw)
        if a.save_stems:
            d = P(a.save_stems)
            d.mkdir(parents=True, exist_ok=True)
            for name, x in (('music', music), ('sfx', sfx)):
                f = Path(tmp) / f'{name}.f32'
                x.astype('<f4').tofile(f)
                run(['-y', '-f', 'f32le', '-ar', sr, '-ac', 2, '-i', f, '-c:a', 'pcm_f32le', d / f'{name}_balanced.wav'])
        m = loudnorm_measure(raw, sr, chain, a.target, a.tp, a.lra)
        ln = (f'loudnorm=I={a.target}:TP={a.tp}:LRA={a.lra}:measured_I={m["input_i"]}:measured_TP={m["input_tp"]}:'
              f'measured_LRA={m["input_lra"]}:measured_thresh={m["input_thresh"]}:offset={m["target_offset"]}:'
              'linear=true:print_format=summary')
        audio = ['-f', 'f32le', '-ar', sr, '-ac', 2, '-i', raw]
        out.parent.mkdir(parents=True, exist_ok=True)
        if video:
            args = ['-y', '-i', video, *audio, '-filter_complex', f'[1:a]{chain}{ln},aresample={sr}[aout]',
                    '-map', '0:v:0', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', a.bitrate,
                    '-movflags', '+faststart', out]
        else:
            codec = ['-c:a', 'pcm_s24le'] if out.suffix.lower() == '.wav' else ['-c:a', 'aac', '-b:a', a.bitrate]
            args = ['-y', *audio, '-af', f'{chain}{ln},aresample={sr}', *codec, out]
        r = run(args)
        if r.returncode:
            raise SystemExit('render failed\n' + r.stderr.decode('utf-8', 'replace')[-1500:])
    final = ebur128(out)
    report['loudness'] = {'before_normalisation': {k: m[k] for k in ('input_i', 'input_tp', 'input_lra')},
                          'final': final}
    odur, _, _ = stream_info(out)
    print(f'\nbefore normalisation: {m["input_i"]} LUFS, true peak {m["input_tp"]} dBTP, LRA {m["input_lra"]} LU')
    print(f'final: {final["integrated_lufs"]} LUFS, true peak {final["true_peak_dbtp"]} dBTP, '
          f'LRA {final["lra_lu"]} LU, {odur:.2f} s')
    warn = []
    if final['integrated_lufs'] is None or abs(final['integrated_lufs'] - a.target) > 1.0:
        warn.append(f'integrated loudness is off the {a.target} LUFS target')
    if final['true_peak_dbtp'] is not None and final['true_peak_dbtp'] > a.tp + 0.5:
        warn.append(f'true peak above {a.tp} dBTP')
    for x in warn:
        print('WARN ', x)
    report['warnings'] = warn
    write_report()
    print('wrote', a.out)
    return report


if __name__ == '__main__':
    main()
