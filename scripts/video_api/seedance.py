# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31",
#     "pillow>=10.1",
# ]
# ///
"""Seedance video-generation client for Volcano Engine Ark (BytePlus ModelArk works too: set ARK_BASE_URL / ARK_MODEL).

Subcommands (run from the project folder or pass --project DIR; every relative path is resolved against it):
  submit    create one task: --mode first | first_last | ref; prompt from a file; images normalised, sent as data URLs
            (the output aspect follows the first frame unless --aspect; frames >5 % off it are refused, not cropped)
  upgrade   turn approved 480p drafts into 1080p (same content, no re-roll); resumable
  poll      wait for tasks, download <gen>/<shot>/<task>.mov and <task>.last.jpg; `poll --pending` resumes everything
  estimate  token formula x list price, before you spend anything
  log       what was submitted, what finished, tokens used and the estimated spend

Every API call is appended to <project>/gen/video_log.jsonl the moment it returns, so a crash after a submit loses
nothing: the task id is on disk before polling starts. The API key is read from the environment or <project>/.env
(ARK_API_KEY) and is never printed or logged. --dry builds and checks the request and prints it with the key, image
data and URL queries hidden, without any network call.

API facts in this file were checked on 2026-10-15 against the Volcano Ark docs for Seedance 2.5.
Model ids, limits and prices change: check the latest docs and your console before relying on them.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------------------------------------------------
# API facts (Seedance 2.5 on Volcano Ark, checked 2026-10-15). Check the latest docs before use.

CHECKED = '2026-10-15'
DEFAULT_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'
# BytePlus ModelArk (international): https://ark.ap-southeast.bytepluses.com/api/v3 (own key, own model ids)
DEFAULT_MODEL = 'doubao-seedance-2-5-260628'
# ^ The Seedance 2.5 model id in the Ark console on the date above (BytePlus lists it as dreamina-seedance-2-5-260628).
#   Ids carry a release-date suffix: copy the exact id from your console's model list, or set ARK_MODEL / --model.
TASKS = '/contents/generations/tasks'
FPS = 24                                    # 2.5 output frame rate; not a request parameter
RESOLUTIONS = ('480p', '720p', '1080p')
ASPECTS = {                                 # output pixel size per aspect and resolution (2.5 docs)
    '16:9': {'480p': (854, 480), '720p': (1280, 720), '1080p': (1920, 1080)},
    '9:16': {'480p': (480, 854), '720p': (720, 1280), '1080p': (1080, 1920)},
    '1:1': {'480p': (640, 640), '720p': (960, 960), '1080p': (1440, 1440)},
    '4:3': {'480p': (752, 560), '720p': (1112, 834), '1080p': (1664, 1248)},
    '3:4': {'480p': (560, 752), '720p': (834, 1112), '1080p': (1248, 1664)},
    '21:9': {'480p': (992, 432), '720p': (1470, 630), '1080p': (2206, 946)},
}
DURATION_RANGE = (4, 30)                    # seconds; -1 lets the model choose
MAX_REF_IMAGES, MAX_REF_VIDEOS = 30, 10
MAX_BODY_BYTES = 64 * 2**20                 # whole request body
IMAGE_SIDE = (300, 6000)                    # px, each side
IMAGE_ASPECT = (0.4, 2.5)                   # width / height
ASPECT_TOLERANCE = 0.05                     # a first/last frame further than 5 % off --aspect is refused (cropping)
VIDEO_MIN_BILLED_S = 4                      # with an input video, short inputs bill like 4 s (vendor price examples)
RESULT_TTL_H = 24                           # result URLs expire after 24 h (and after 100 downloads)
DRAFT_TTL_DAYS = 7                          # task records, and so draft ids, live 7 days

# List prices, CNY per million tokens, Volcano Ark online inference, checked 2026-10-15 (pricing page of 2026-10-10).
# Key: (output resolution, input contains a video). Only successful tasks are billed; usage.completion_tokens is
# what you pay for. Order matters: the more specific model names come first.
PRICES = {
    'seedance-2-0-fast': {('480p', False): 37.0, ('720p', False): 37.0, ('480p', True): 22.0, ('720p', True): 22.0},
    'seedance-2-0-mini': {('480p', False): 23.0, ('720p', False): 23.0, ('480p', True): 14.0, ('720p', True): 14.0},
    'seedance-2-5': {('480p', False): 70.0, ('720p', False): 70.0, ('1080p', False): 77.0,
                     ('480p', True): 42.0, ('720p', True): 42.0, ('1080p', True): 46.0},
    'seedance-2-0': {('480p', False): 46.0, ('720p', False): 46.0, ('1080p', False): 51.0,
                     ('480p', True): 28.0, ('720p', True): 28.0, ('1080p', True): 31.0},
}

# Seedance 2.5 prompt syntax: () music, <> sound effects, {} dialogue, 【】 on-screen subtitles. Even with audio off they
# can change the picture (subtitles), so they are refused unless --allow-markup.
MARKUP = '【】()<>{}（）＜＞｛｝'
# Words that can turn a reference task into an edit / extend task (warning only; longest first).
FLIP_WORDS = ('编辑视频', '向前延长', '向后延长', '延长', '续写', '延续', '替换', '删除', '去掉', '改成',
              'edit the video', 'extend the video')
REF = re.compile(r'@(图片|图像|视频|音频|image|video|audio)\s*(\d+)', re.I)
REF_RANGE = re.compile(r'@(图片|图像|视频)\s*(\d+)\s*(?:至|到|~|-)\s*@?(?:图片|图像|视频)?\s*(\d+)')
KIND = {'图片': 'image', '图像': 'image', 'image': 'image', '视频': 'video', 'video': 'video', '音频': 'audio',
        'audio': 'audio'}
SHOT_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
FINAL = ('done', 'failed', 'expired', 'cancelled')

# ---------------------------------------------------------------------------------------------------------------------
# Small helpers


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def rel(path, root):
    """Path shown relative to the project when it is inside it (keeps user folders out of logs and output)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def is_url(s):
    return str(s).startswith(('http://', 'https://', 'asset://'))


SIGNED = re.compile(r'(?i)((?:x-(?:tos|amz)-)?(?:signature|credential|security-token|token|key)(?:=|%3d))[^&\s"\'\\]+')


def scrub(text):
    """Drop query strings from URLs in an error text (presigned links), then any credential value left over."""
    text = re.sub(r"(https?:(?:\\?/){2}[^\s\"'?]+)\?[^\s\"']*", r'\1?<hidden>', str(text))
    return SIGNED.sub(r'\1<hidden>', text)


def redact_url(u):
    if u.startswith('data:'):
        head = u[:u.find(',')] if ',' in u else u[:30]
        return f'<{head}, {len(u) * 3 // 4 // 1024} KB>'
    p = urlparse(u)
    if p.scheme == 'asset':
        return u
    return f'{p.scheme}://{p.netloc}{p.path}' + ('?<query hidden>' if p.query else '')


def load_env(project):
    """KEY=VALUE lines of <project>/.env into os.environ (variables already set win)."""
    env = Path(project) / '.env'
    if not env.is_file():
        return
    for line in env.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need_key(project):
    load_env(project)
    key = os.environ.get('ARK_API_KEY', '').strip()
    if not key:
        raise SystemExit('ARK_API_KEY is not set. Put a line ARK_API_KEY=... in <project>/.env (and list .env in '
                         '.gitignore) or set it in the environment. Nothing was sent.')
    return key


def price_table(model):
    m = model.lower()
    for name, table in PRICES.items():
        if name in m:
            return name, table
    return None, None


def est_tokens(out_s, w, h, in_video_s=0.0, fps=FPS):
    """Vendor formula: (input video s + output s) x out width x out height x fps / 1024."""
    if in_video_s:
        in_video_s = max(in_video_s, VIDEO_MIN_BILLED_S)
    return (in_video_s + out_s) * w * h * fps / 1024


def est_cost(model, resolution, has_video, tokens, price=None):
    """(CNY, note). price overrides the table (CNY per million tokens)."""
    if price is None:
        name, table = price_table(model)
        if not table or (resolution, has_video) not in table:
            return None, f'no list price for {model} at {resolution}: pass --price'
        price = table[(resolution, has_video)]
    return tokens * price / 1e6, f'{price:g} CNY/M tokens'


# ---------------------------------------------------------------------------------------------------------------------
# Log: one JSON object per line, appended and flushed at once. It is also the task registry (poll --pending, upgrade).


class Log:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {'time': now(), **record}
        with open(self.path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def records(self):
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def tasks(self):
        """task id -> {'submit': first submit/upgrade record, 'final': last final record or None}."""
        st = {}
        for r in self.records():
            t = r.get('task')
            if not t:
                continue
            if r.get('action') in ('submit', 'upgrade'):
                st.setdefault(t, {'submit': r, 'final': None})
            elif r.get('action') in FINAL and t in st:
                st[t]['final'] = r
            elif r.get('action') in FINAL:
                st[t] = {'submit': None, 'final': r}
        return st


# ---------------------------------------------------------------------------------------------------------------------
# HTTP with retries. GETs are retried on any transient error. A POST is retried only when the request surely did not
# create a task (429, 500, 503, or the connection never opened); after a read timeout, 502 or 504 the task may exist,
# so it stops and asks you to look before paying twice.

RETRY_GET = {429, 500, 502, 503, 504}
RETRY_POST = {429, 500, 503}


class Uncertain(Exception):
    """The POST may have reached the server: do not resend blindly."""


def _not_sent(exc):
    if isinstance(exc, requests.ConnectTimeout):
        return True
    text = repr(exc)
    return isinstance(exc, requests.ConnectionError) and any(
        k in text for k in ('NewConnectionError', 'NameResolutionError', 'Failed to resolve', 'getaddrinfo'))


def call(method, url, key, body=None, timeout=60, attempts=6, sleep=time.sleep):
    post = method == 'POST'
    retry = RETRY_POST if post else RETRY_GET
    delay, last = 5, ''
    for i in range(attempts):
        try:
            r = requests.request(method, url, headers={'Authorization': f'Bearer {key}',
                                                       'Content-Type': 'application/json'},
                                 json=body, timeout=timeout)
        except requests.RequestException as e:
            if post and not _not_sent(e):
                raise Uncertain(type(e).__name__) from None
            last, wait = type(e).__name__, delay
        else:
            if r.status_code not in retry:
                if post and r.status_code in (502, 504):
                    raise Uncertain(f'HTTP {r.status_code}')
                return r
            last = f'HTTP {r.status_code}'
            ra = r.headers.get('Retry-After', '')
            wait = int(ra) if ra.isdigit() else (60 if r.status_code == 429 else delay)
        if i == attempts - 1:
            break
        print(f'  retry {i + 1}/{attempts - 1} in {wait} s ({last})', flush=True)
        sleep(wait)
        delay = min(delay * 2, 120)
    raise SystemExit(f'{method} gave up after {attempts} attempts ({last}). Nothing else was sent.')


def download(url, path, attempts=4, sleep=time.sleep):
    """Stream one result to path (via .part). Errors show the status or exception type only: the URL is presigned."""
    tmp = path.with_name(path.name + '.part')
    last = ''
    for i in range(attempts):
        try:
            with requests.get(url, stream=True, timeout=600) as r:
                if r.status_code != 200:
                    last = f'HTTP {r.status_code}'
                    if r.status_code in (403, 404, 410):
                        break                      # expired link (24 h) or download cap reached
                else:
                    with open(tmp, 'wb') as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                    tmp.replace(path)
                    return None
        except Exception as e:     # noqa: BLE001 - the text of most exceptions holds the presigned URL
            last = type(e).__name__
        if i < attempts - 1:
            sleep(5 * (i + 1))
    tmp.unlink(missing_ok=True)
    return last


# ---------------------------------------------------------------------------------------------------------------------
# Images


def to_rgb(im):
    im = ImageOps.exif_transpose(im)
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel('A'))
        return bg
    return im.convert('RGB')


def orient(w, h):
    return 'portrait' if h > w else 'landscape' if w > h else 'square'


def aspect_off(r, target):
    """Relative difference of two width/height ratios (0.05 = 5 %)."""
    return max(r, target) / min(r, target) - 1


def nearest_aspect(w, h):
    """(aspect key, relative difference) of the supported output aspect closest to w x h."""
    r = w / h
    key = min(ASPECTS, key=lambda k: aspect_off(r, ASPECTS[k]['1080p'][0] / ASPECTS[k]['1080p'][1]))
    box = ASPECTS[key]['1080p']
    return key, aspect_off(r, box[0] / box[1])


def crop_to_ratio(im, ratio):
    """Centre crop to width/height = ratio. Returns (image, note or None)."""
    w, h = im.size
    if abs(w / h - ratio) < 0.004:
        return im, None
    if w / h > ratio:
        nw = round(h * ratio)
        x = (w - nw) // 2
        return im.crop((x, 0, x + nw, h)), f'centre-cropped {w}x{h} -> {nw}x{h}'
    nh = round(w / ratio)
    y = (h - nh) // 2
    return im.crop((0, y, w, y + nh)), f'centre-cropped {w}x{h} -> {w}x{nh}'


def encode_jpeg(im, quality=92):
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=quality, subsampling=0)     # 4:4:4 keeps thin lines and small type crisp
    data = buf.getvalue()
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode(), data


def prepare_image(spec, role, index, ctx, problems, warns):
    """One image asset. Frames (first/last) are centre-cropped to the output aspect and resized to its 1080p size, so
    first and last match exactly and the 1080p upgrade gets full-size inputs; a frame more than 5 % off that aspect
    is refused unless --allow-crop (a vertical keyframe sent as 16:9 would lose two thirds of its picture).
    Reference images are only shrunk to fit 1080p in their own orientation (never cropped, so no product detail is
    cut off) unless --crop-refs."""
    a = {'kind': 'image', 'role': role, 'source': spec, 'size': None, 'note': ''}
    if is_url(spec):
        a.update(url=spec, shown=redact_url(spec), note='sent as given (not normalised)')
        if role in ('first_frame', 'last_frame'):
            warns.append(f'{role}: a URL is sent as is; make sure it already has the output aspect '
                         f'and, for first_last, the same size as the other frame')
        return a
    path = ctx['project'] / spec
    if not path.is_file():
        problems.append(f'{role} {spec}: file not found')
        return None
    a['source'] = rel(path, ctx['project'])      # never an absolute user path in the log
    try:
        im = to_rgb(Image.open(path))
    except Exception as e:     # noqa: BLE001
        problems.append(f'{role} {spec}: not a readable image ({type(e).__name__})')
        return None
    src = im.size
    notes = []
    if role in ('first_frame', 'last_frame') or ctx['crop_refs']:
        box = ASPECTS[ctx['aspect']]['1080p']
        off = aspect_off(im.width / im.height, box[0] / box[1])
        im, note = crop_to_ratio(im, box[0] / box[1])
        if note:
            notes.append(note)
        if role in ('first_frame', 'last_frame') and off > ASPECT_TOLERANCE and not ctx.get('allow_crop'):
            problems.append(f'{role} {spec}: {src[0]}x{src[1]} ({orient(*src)}) is {off:.0%} off --aspect '
                            f'{ctx["aspect"]} and would be {note}, then scaled to {box[0]}x{box[1]}. Give frames '
                            f'in the output aspect (leave --aspect out and the first frame sets it), or pass '
                            f'--allow-crop if this crop is intended')
        if im.width < box[0] * 0.95:
            warns.append(f'{role} {spec}: upscaled from {im.width}x{im.height} to {box[0]}x{box[1]}; '
                         'a larger source keeps more detail')
        if im.size != box:
            im = im.resize(box, Image.LANCZOS)
    else:
        box = (1920, 1080) if im.width >= im.height else (1080, 1920)
        s = min(1.0, box[0] / im.width, box[1] / im.height)
        if s < 1:
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    w, h = im.size
    if not (IMAGE_SIDE[0] <= min(w, h) and max(w, h) <= IMAGE_SIDE[1] and IMAGE_ASPECT[0] <= w / h <= IMAGE_ASPECT[1]):
        problems.append(f'{role} {spec}: {w}x{h} is outside the image limits (sides {IMAGE_SIDE[0]}-{IMAGE_SIDE[1]} px, '
                        f'aspect {IMAGE_ASPECT[0]}-{IMAGE_ASPECT[1]})')
    url, data = encode_jpeg(im)
    sha = hashlib.sha256(data).hexdigest()
    saved = None
    if ctx.get('sent_dir'):
        ctx['sent_dir'].mkdir(parents=True, exist_ok=True)
        saved = ctx['sent_dir'] / f'{index:02d}_{role}_{Path(spec).stem}.jpg'
        saved.write_bytes(data)
    a.update(url=url, size=[w, h], sha256=sha, bytes=len(data),
             shown=f'<base64 jpeg {w}x{h}, {len(data) // 1024} KB, sha256 {sha[:12]}>',
             note='; '.join(notes + ([f'from {src[0]}x{src[1]}'] if src != (w, h) and not notes else [])),
             saved=rel(saved, ctx['project']) if saved else None)
    return a


def prepare_video(spec, problems):
    if not str(spec).startswith(('http://', 'https://', 'asset://')):
        problems.append(f'reference video {spec}: must be a public http(s) URL or asset://ID (base64 video is not '
                        'accepted); upload it to object storage with public read first')
        return None
    return {'kind': 'video', 'role': 'reference_video', 'source': redact_url(spec), 'url': spec,
            'shown': redact_url(spec), 'size': None, 'note': 'URL'}


def probe(url):
    """Range-GET the first 64 KB. Returns None when alive, else a short reason (no URL text)."""
    if url.startswith('asset://'):
        return None
    try:
        with requests.get(url, headers={'Range': 'bytes=0-65535'}, stream=True, timeout=30) as r:
            return None if r.status_code in (200, 206) else f'HTTP {r.status_code}'
    except Exception as e:     # noqa: BLE001
        return type(e).__name__


# ---------------------------------------------------------------------------------------------------------------------
# Prompt checks


def cited(text):
    got = {'image': set(), 'video': set(), 'audio': set()}
    for kind, n in REF.findall(text):
        got[KIND[kind.lower() if kind.isascii() else kind]].add(int(n))
    for kind, a, b in REF_RANGE.findall(text):
        got[KIND[kind]].update(range(int(a), int(b) + 1))
    return got


def prompt_checks(text, mode, n_images, n_videos, allow_markup=False):
    problems, warns = [], []
    if not text.strip():
        problems.append('prompt is empty')
        return problems, warns
    marks = sorted({ch for ch in text if ch in MARKUP})
    if marks:
        msg = (f'prompt contains {" ".join(marks)}: Seedance 2.5 reads () as music, <> as sound effects, {{}} as '
               'dialogue and 【】 as on-screen subtitles')
        (warns if allow_markup else problems).append(msg + ('' if allow_markup else ' (rephrase, or --allow-markup)'))
    if mode == 'ref':
        low = text.lower()
        rest = low
        for w in FLIP_WORDS:
            if w in rest:
                warns.append(f'prompt has "{w}": words like this can turn a reference task into an edit/extend task')
                rest = rest.replace(w, ' ')
    if re.search(r'生成一段[^。\n]{0,12}?\d+\s*秒|\b\d+[- ]second (video|clip)\b', text, re.I):
        warns.append('prompt states a duration; duration is a request parameter, leave it out of the text')
    got = cited(text)
    if mode == 'ref':
        for kind, n in (('image', n_images), ('video', n_videos)):
            extra = sorted(i for i in got[kind] if not 1 <= i <= n)
            if extra:
                problems.append(f'prompt cites {kind} #{", #".join(map(str, extra))} but only {n} {kind}(s) are attached')
            unused = [i for i in range(1, n + 1) if i not in got[kind]]
            if unused:
                warns.append(f'{kind} #{", #".join(map(str, unused))} attached but never named in the prompt '
                             '(say what each reference is for, e.g. @图片1 / @image 1)')
        if got['audio']:
            problems.append('prompt cites an audio reference, but this client sends none')
    elif any(got.values()):
        warns.append('prompt uses @ references, but first/first_last tasks have no reference list')
    limit = 1500 if mode == 'ref' else 600
    if len(text) > limit:
        warns.append(f'prompt is {len(text)} characters; long prompts dilute attention (vendor tip: about 500 Chinese '
                     'characters or 1000 English words)')
    return problems, warns


# ---------------------------------------------------------------------------------------------------------------------
# Request bodies


def build_body(mode, prompt, assets, *, model, duration, resolution, draft, aspect, task_type='reference',
               generate_audio=False, watermark=False, output_format='mov', return_last_frame=True, extra=None):
    content = [{'type': 'text', 'text': prompt}]
    for a in assets:
        t = 'image_url' if a['kind'] == 'image' else 'video_url'
        content.append({'type': t, t: {'url': a['url']}, 'role': a['role']})
    body = {'model': model, 'content': content, 'duration': duration,
            'resolution': '480p' if draft else resolution,
            # first-frame and first+last tasks only accept adaptive: the output follows the first frame's aspect
            'ratio': 'adaptive' if mode in ('first', 'first_last') else aspect,
            'generate_audio': generate_audio, 'watermark': watermark,
            'output_format': output_format,       # mov = H.264 4:4:4, uncompressed audio; 2.5 mp4 at 1080p is 10-bit HEVC
            'return_last_frame': return_last_frame}
    if draft:
        body['draft'] = True                     # billed as 480p; upgrade later with `upgrade`
    if mode == 'ref' and task_type != 'auto':
        body['omni_reference_task_type'] = task_type   # explicit: validated at submit, not after queueing
    if extra:
        body.update(extra)
    return body


def upgrade_body(draft_task, model, output_format='mov', return_last_frame=True):
    """Draft -> 1080p. Prompt, inputs, duration, ratio, seed, generate_audio and the task type are reused and must not
    be re-sent (re-sending is an error); output_format and return_last_frame fall back to mp4 / false unless given."""
    return {'model': model, 'content': [{'type': 'draft_task', 'draft_task': {'id': draft_task}}],
            'resolution': '1080p', 'return_last_frame': return_last_frame, 'output_format': output_format}


def redacted(body, assets=()):
    """Copy of a body that is safe to print: prompt replaced by its length, every URL by its short label."""
    b = json.loads(json.dumps(body))
    shown = iter([a['shown'] for a in assets])
    for item in b.get('content', []):
        if item.get('type') == 'text':
            item['text'] = f'<prompt, {len(item["text"])} chars, printed above>'
        for t in ('image_url', 'video_url'):
            if t in item:
                item[t]['url'] = next(shown, None) or redact_url(item[t]['url'])
    return b


# ---------------------------------------------------------------------------------------------------------------------
# Context


def make_ctx(a):
    project = Path(a.project).resolve()
    load_env(project)
    gen = project / a.gen_dir
    return {'project': project, 'gen': gen, 'log': Log(gen / 'video_log.jsonl'),
            'base': (a.base_url or os.environ.get('ARK_BASE_URL') or DEFAULT_BASE_URL).rstrip('/'),
            'model': a.model or os.environ.get('ARK_MODEL') or DEFAULT_MODEL}


def check_shot(shot):
    if not shot or not SHOT_ID.match(shot):
        raise SystemExit(f'--shot {shot!r}: use letters, digits, _ . - (it names the folder <gen>/<shot>/)')


# ---------------------------------------------------------------------------------------------------------------------
# submit


def read_prompt(a, project):
    if a.prompt_file:
        p = project / a.prompt_file
        try:
            return p.read_text(encoding='utf-8-sig').strip(), a.prompt_file
        except (OSError, UnicodeDecodeError) as e:
            raise SystemExit(f'--prompt-file {a.prompt_file}: cannot read as UTF-8 text ({type(e).__name__})')
    return (a.prompt or '').strip(), '--prompt'


def resolve_aspect(a, ctx, problems, warns):
    """Output aspect: --aspect when given; for first / first_last the first frame's own aspect (the task's ratio is
    adaptive: the video follows the first frame); else 16:9 with a warning. Returns (aspect, how it was chosen)."""
    if a.aspect:
        return a.aspect, '--aspect'
    if a.mode in ('first', 'first_last') and a.first and not is_url(a.first):
        path = ctx['project'] / a.first
        try:
            with Image.open(path) as im:
                w, h = ImageOps.exif_transpose(im).size
        except Exception:     # noqa: BLE001 - reported by prepare_image (missing or unreadable file)
            return '16:9', 'default'
        key, off = nearest_aspect(w, h)
        if off > ASPECT_TOLERANCE:
            problems.append(f'first frame {a.first}: {w}x{h} is no supported aspect ({", ".join(ASPECTS)}; nearest '
                            f'{key}, {off:.0%} off). Normalise the keyframes to the output size first '
                            f'(scripts/qc/normalise_keyframes.py), or pass --aspect and --allow-crop')
        return key, f'first frame {w}x{h}'
    if a.mode == 'ref':
        warns.append('no --aspect: ratio 16:9 (default for ref mode); a vertical film needs --aspect 9:16')
    return '16:9', 'default'


def plan_submit(a, ctx):
    """Everything up to the network call. Returns (body, assets, prompt, problems, warnings)."""
    problems, warns = [], []
    prompt, source = read_prompt(a, ctx['project'])
    model = ctx['model']
    aspect, how = resolve_aspect(a, ctx, problems, warns)
    ctx.update(aspect=aspect, aspect_from=how, crop_refs=a.crop_refs, allow_crop=a.allow_crop,
               sent_dir=ctx['gen'] / a.shot / 'sent')
    assets = []
    if a.mode == 'first':
        if not a.first or a.last or a.image or a.video_url:
            problems.append('--mode first takes exactly --first IMAGE')
        specs = [(a.first, 'first_frame')] if a.first else []
    elif a.mode == 'first_last':
        if not (a.first and a.last) or a.image or a.video_url:
            problems.append('--mode first_last takes exactly --first IMAGE and --last IMAGE')
        specs = [(s, r) for s, r in ((a.first, 'first_frame'), (a.last, 'last_frame')) if s]
    else:
        if a.first or a.last:
            problems.append('--mode ref takes --image (repeatable) and --video-url, not --first/--last '
                            '(first/last frames and references cannot be mixed in one task)')
        if not (a.image or a.video_url):
            problems.append('--mode ref needs at least one --image or --video-url')
        if len(a.image or []) > MAX_REF_IMAGES:
            problems.append(f'{len(a.image)} reference images (limit {MAX_REF_IMAGES})')
        if len(a.video_url or []) > MAX_REF_VIDEOS:
            problems.append(f'{len(a.video_url)} reference videos (limit {MAX_REF_VIDEOS})')
        specs = [(s, 'reference_image') for s in a.image or []]
    for i, (spec, role) in enumerate(specs, 1):
        asset = prepare_image(spec, role, i, ctx, problems, warns)
        if asset:
            assets.append(asset)
    for spec in (a.video_url or []) if a.mode == 'ref' else []:
        asset = prepare_video(spec, problems)
        if asset:
            assets.append(asset)
    if a.mode == 'first_last' and len(assets) == 2 and all(x['size'] for x in assets) and assets[0]['size'] != assets[1]['size']:
        problems.append(f'first and last frame differ in size {assets[0]["size"]} vs {assets[1]["size"]}')
    n_img = sum(x['kind'] == 'image' for x in assets)
    n_vid = sum(x['kind'] == 'video' for x in assets)
    p2, w2 = prompt_checks(prompt, a.mode, n_img, n_vid, a.allow_markup)
    problems += p2
    warns += w2
    is25 = 'seedance-2-5' in model.lower()
    if a.duration != -1 and not (DURATION_RANGE[0] <= a.duration <= DURATION_RANGE[1]):
        msg = (f'duration {a.duration} s: Seedance 2.5 takes {DURATION_RANGE[0]}-{DURATION_RANGE[1]} s or -1; '
               'generate a little longer and trim in the edit')
        (problems if is25 else warns).append(msg)
    if a.draft and a.resolution not in ('480p', None):
        problems.append('--draft is always 480p; drop --resolution or drop --draft')
    if a.draft and not is25:
        warns.append(f'draft mode is documented for Seedance 2.5 only (model {model})')
    resolution = '480p' if a.draft else (a.resolution or '1080p')
    extra = None
    if a.extra:
        try:
            extra = json.loads(a.extra)
        except ValueError:
            extra = None
        if not isinstance(extra, dict):
            extra = None
            problems.append('--extra must be a JSON object, e.g. {"priority": 5}')
    body = build_body(a.mode, prompt, assets, model=model, duration=a.duration, resolution=resolution,
                      draft=a.draft, aspect=aspect, task_type=a.task_type, generate_audio=a.audio,
                      watermark=a.watermark, output_format=a.format, return_last_frame=not a.no_last_frame,
                      extra=extra)
    size = len(json.dumps(body))
    if size > MAX_BODY_BYTES:
        problems.append(f'request body is {size / 2**20:.1f} MB (limit 64 MB): fewer or smaller images')
    return body, assets, (prompt, source), problems, warns


def print_cost(model, duration, aspect, has_video, video_s, resolution, draft, price=None):
    """Print the estimate for this request (and for its later 1080p upgrade when it is a draft); return this one."""
    if duration == -1:
        print('COST   duration -1 (model decides): run `estimate --duration N` for a figure')
        return None
    vs = (video_s if video_s is not None else duration) if has_video else 0.0
    steps = (('480p', 'draft'), ('1080p', 'later upgrade')) if draft else ((resolution, 'this task'),)
    parts, this = [], None
    for res, label in steps:
        w, h = ASPECTS[aspect][res]
        tok = est_tokens(duration, w, h, vs)
        cny, note = est_cost(model, res, has_video, tok, price)
        this = cny if this is None else this
        parts.append(f'{label} {res}: {tok:,.0f} tokens ≈ {cny:.2f} CNY ({note})' if cny is not None
                     else f'{label} {res}: {note}')
    print('COST   ' + ' | '.join(parts))
    print(f'       list price checked {CHECKED}; the bill follows usage.completion_tokens'
          + ('; input video length assumed = output, pass --video-seconds' if has_video and video_s is None else ''))
    return this


def cmd_submit(a):
    ctx = make_ctx(a)
    check_shot(a.shot)
    body, assets, (prompt, source), problems, warns = plan_submit(a, ctx)
    ratio = body['ratio'] + (f' (frames normalised to {ctx["aspect"]}, from {ctx["aspect_from"]})'
                             if body['ratio'] == 'adaptive' else '')
    print(f'SUBMIT shot {a.shot}  mode {a.mode}  model {body["model"]}  {body["resolution"]}'
          f'{" draft" if a.draft else ""}  duration {body["duration"]} s  ratio {ratio}')
    print(f'\nPROMPT ({len(prompt)} chars, {source})\n{prompt}\n\nASSETS')
    for i, x in enumerate(assets, 1):
        print(f'  {i}. {x["role"]:<16} {x["source"] if not is_url(x["source"]) else x["shown"]}  ->  {x["shown"]}'
              + (f'  [{x["note"]}]' if x.get('note') else '') + (f'  saved {x["saved"]}' if x.get('saved') else ''))
    print('\nBODY (key, image data and URL queries hidden)')
    print(json.dumps(redacted(body, assets), ensure_ascii=False, indent=1))
    has_video = any(x['kind'] == 'video' for x in assets)
    est = print_cost(body['model'], body['duration'], ctx['aspect'], has_video, a.video_seconds, body['resolution'],
                     a.draft, a.price)
    for w in warns:
        print('WARN  ', w)
    if problems:
        print(f'\nREFUSED: {len(problems)} problem(s), nothing sent')
        for p in problems:
            print('  -', p)
        raise SystemExit(1)
    if a.dry:
        print('\nOK: every check passed (--dry: nothing sent)')
        return None
    key = need_key(ctx['project'])
    if not a.no_probe:
        for x in assets:
            if is_url(x['url']):
                why = probe(x['url'])
                if why:
                    raise SystemExit(f'{x["role"]} {x["shown"]}: not reachable ({why}); expired link? Nothing sent.')
    base = {'shot': a.shot, 'mode': a.mode, 'model': body['model'], 'resolution': body['resolution'],
            'draft': a.draft, 'duration': body['duration'], 'ratio': body['ratio'], 'aspect': ctx['aspect'],
            'has_video': has_video, 'video_seconds': a.video_seconds, 'output_format': a.format,
            'prompt_source': source, 'prompt': prompt, 'est_cny': est,
            'inputs': [{'role': x['role'], 'source': redact_url(x['source']) if is_url(x['source']) else x['source'],
                        'size': x.get('size'), 'sha256': x.get('sha256'), 'saved': x.get('saved')} for x in assets]}
    try:
        r = call('POST', ctx['base'] + TASKS, key, body, timeout=120)
    except Uncertain as e:
        ctx['log'].append({**base, 'action': 'submit_uncertain', 'error': str(e)})
        raise SystemExit(f'Submit outcome unknown ({e}): the task may exist. Check the task list in the console '
                         'before submitting again (a second submit is billed again).')
    task = r.json().get('id') if r.status_code == 200 else None
    if not task:
        ctx['log'].append({**base, 'action': 'submit_error', 'status': r.status_code, 'error': scrub(r.text[:800])})
        raise SystemExit(f'HTTP {r.status_code}: {scrub(r.text[:600])}')
    ctx['log'].append({**base, 'action': 'submit', 'task': task})
    print(f'\nTASK {a.shot} {task} ({body["resolution"]}{" draft" if a.draft else ""}) logged to '
          f'{rel(ctx["log"].path, ctx["project"])}', flush=True)
    if a.no_wait:
        print('Not waiting. Later: uv run seedance.py poll --pending')
        return task
    poll_tasks({task: a.shot}, ctx, key, a.every, a.timeout)
    return task


# ---------------------------------------------------------------------------------------------------------------------
# upgrade


def parse_pairs(a):
    pairs = []
    for item in a.pairs or []:
        if '=' not in item:
            raise SystemExit(f'{item!r}: write SHOT=DRAFT_TASK_ID')
        s, t = item.split('=', 1)
        pairs.append((s.strip(), t.strip()))
    if a.from_json:
        data = json.loads((Path(a.project) / a.from_json).read_text(encoding='utf-8-sig'))
        if isinstance(data, dict):
            pairs += [(str(s), str(t)) for s, t in data.items()]
        else:
            pairs += [(str(d['shot']), str(d['task'])) for d in data]
    if not pairs:
        raise SystemExit('nothing to upgrade: give SHOT=DRAFT_TASK_ID pairs or --from-json FILE')
    for s, _ in pairs:
        check_shot(s)
    return pairs


def cmd_upgrade(a):
    ctx = make_ctx(a)
    pairs = parse_pairs(a)
    tasks = ctx['log'].tasks()
    upgraded = {st['submit'].get('draft_task'): t for t, st in tasks.items()
                if st['submit'] and st['submit'].get('action') == 'upgrade'}
    plan, total = [], 0.0
    for shot, draft in pairs:
        src = (tasks.get(draft) or {}).get('submit') or {}
        model = src.get('model') or ctx['model']
        note = []
        if not src:
            note.append('draft not in this log: duration/aspect unknown, cost not estimated')
        elif not src.get('draft'):
            note.append('WARNING: the log says this task was not a draft')
        else:
            age = (datetime.now().astimezone() - datetime.fromisoformat(src['time'])).total_seconds() / 86400
            if age > DRAFT_TTL_DAYS:
                note.append(f'WARNING: draft is {age:.1f} days old; task records live {DRAFT_TTL_DAYS} days')
        est = None
        if src.get('duration', -1) != -1 and src.get('aspect') in ASPECTS:
            w, h = ASPECTS[src['aspect']]['1080p']
            vs = src.get('video_seconds') or (src['duration'] if src.get('has_video') else 0)
            est, _ = est_cost(model, '1080p', bool(src.get('has_video')),
                              est_tokens(src['duration'], w, h, vs if src.get('has_video') else 0))
            total += est or 0
        done = upgraded.get(draft)
        if done and not a.again:
            note.append(f'already upgraded -> {done} (use --again to pay for another)')
        plan.append((shot, draft, model, done if not a.again else None, est, note))
    print(f'UPGRADE {len(plan)} draft(s) to 1080p   [list price checked {CHECKED}]')
    for shot, draft, model, done, est, note in plan:
        print(f'  {shot:<14} {draft}  {"≈ %.2f CNY" % est if est is not None else "cost ?":<14} {"; ".join(note)}')
        if not done:
            print('     body', json.dumps(upgrade_body(draft, model, a.format), ensure_ascii=False))
    print(f'  total of new upgrades ≈ {sum(p[4] or 0 for p in plan if not p[3]):.2f} CNY (estimate)')
    if a.dry:
        print('\n--dry: nothing sent')
        return []
    key = need_key(ctx['project'])
    wait = {}
    for shot, draft, model, done, est, _ in plan:
        if done:
            if not (tasks.get(done) or {}).get('final'):
                wait[done] = shot
            continue
        base = {'shot': shot, 'draft_task': draft, 'model': model, 'resolution': '1080p', 'output_format': a.format,
                'has_video': bool(((tasks.get(draft) or {}).get('submit') or {}).get('has_video')), 'est_cny': est}
        try:
            r = call('POST', ctx['base'] + TASKS, key, upgrade_body(draft, model, a.format), timeout=120)
        except Uncertain as e:
            ctx['log'].append({**base, 'action': 'submit_uncertain', 'error': str(e)})
            print(f'  {shot}: outcome unknown ({e}); check the console before retrying this one', flush=True)
            continue
        task = r.json().get('id') if r.status_code == 200 else None
        if not task:
            ctx['log'].append({**base, 'action': 'submit_error', 'status': r.status_code, 'error': scrub(r.text[:800])})
            print(f'  {shot}: HTTP {r.status_code}: {scrub(r.text[:400])}', flush=True)
            continue
        ctx['log'].append({**base, 'action': 'upgrade', 'task': task})
        print(f'  TASK {shot} {draft} -> {task} (1080p)', flush=True)
        wait[task] = shot
    if wait and not a.no_wait:
        poll_tasks(wait, ctx, key, a.every, a.timeout)
    return list(wait)


# ---------------------------------------------------------------------------------------------------------------------
# poll


def save_results(d, task, shot, ctx, fmt):
    out = ctx['gen'] / shot
    out.mkdir(parents=True, exist_ok=True)
    c = d.get('content') or {}
    saved, errors = [], []
    ext = (d.get('output_format') or fmt or Path(urlparse(c.get('video_url', '')).path).suffix.lstrip('.') or 'mp4')
    for key_, name in (('video_url', f'{task}.{ext}'), ('last_frame_url', f'{task}.last.jpg')):
        if not c.get(key_):
            continue
        path = out / name
        if path.is_file() and path.stat().st_size > 0:
            saved.append(rel(path, ctx['project']))
            continue
        err = download(c[key_], path)
        if err:
            errors.append(f'{name}: {err}')
        else:
            saved.append(rel(path, ctx['project']))
    return saved, errors


def poll_tasks(tasks, ctx, key, every=15, timeout_min=60):
    """tasks: {task id: shot}. Returns {task: final status}."""
    states = ctx['log'].tasks()
    pending, result = dict(tasks), {}
    deadline = time.monotonic() + timeout_min * 60
    last_status = {}
    while pending:
        for task, shot in list(pending.items()):
            r = call('GET', f'{ctx["base"]}{TASKS}/{task}', key, timeout=60)
            if r.status_code in (401, 403):
                raise SystemExit(f'HTTP {r.status_code}: the key was refused (check ARK_API_KEY and the region)')
            if r.status_code == 404:
                print(f'  {shot} {task}: not found (records are kept {DRAFT_TTL_DAYS} days; wrong region?)', flush=True)
                result[task] = 'not_found'
                del pending[task]
                continue
            if r.status_code != 200:
                print(f'  {shot} {task}: HTTP {r.status_code} {scrub(r.text[:200])}', flush=True)
                continue
            d = r.json()
            st = d.get('status')
            if st != last_status.get(task):
                print(f'STATUS {shot} {task} {st}', flush=True)
                last_status[task] = st
            sub = (states.get(task) or {}).get('submit') or {}
            if st == 'succeeded':
                saved, errors = save_results(d, task, shot, ctx, sub.get('output_format'))
                usage = d.get('usage') or {}
                tok = usage.get('completion_tokens')
                res = d.get('resolution') or sub.get('resolution')
                model = d.get('model') or sub.get('model') or ctx['model']
                cny = est_cost(model, res, bool(sub.get('has_video')), tok)[0] if tok and res else None
                ctx['log'].append({'action': 'done', 'shot': shot, 'task': task, 'model': model, 'resolution': res,
                                   'ratio': d.get('ratio'), 'duration': d.get('duration'), 'usage': usage,
                                   'cost_cny_list': round(cny, 2) if cny is not None else None,
                                   'files': saved, 'download_errors': errors or None})
                for f in saved:
                    print('SAVED ', f, flush=True)
                for e in errors:
                    print(f'DOWNLOAD FAILED {e} (links live {RESULT_TTL_H} h: rerun `poll {task}` soon)', flush=True)
                result[task] = 'succeeded' if not errors else 'download_failed'
                del pending[task]
            elif st in ('failed', 'expired', 'cancelled'):
                err = scrub(json.dumps(d.get('error'), ensure_ascii=False))
                ctx['log'].append({'action': st, 'shot': shot, 'task': task, 'error': err})
                print(f'{st.upper()} {shot} {task}: {err}', flush=True)
                result[task] = st
                del pending[task]
        if pending:
            if time.monotonic() > deadline:
                print(f'Still running after {timeout_min} min: {", ".join(pending)}. '
                      'Nothing is lost; later run: uv run seedance.py poll --pending', flush=True)
                break
            time.sleep(every)
    return result


def cmd_poll(a):
    ctx = make_ctx(a)
    states = ctx['log'].tasks()
    tasks = {}
    if a.pending:
        tasks = {t: (st['submit'] or {}).get('shot', 'misc') for t, st in states.items()
                 if st['submit'] and not st['final']}
        if not tasks:
            print('No pending tasks in', rel(ctx['log'].path, ctx['project']))
            return {}
    for t in a.tasks or []:
        tasks[t] = a.shot or ((states.get(t) or {}).get('submit') or {}).get('shot') or 'misc'
    if not tasks:
        raise SystemExit('give task ids or --pending')
    key = need_key(ctx['project'])
    print(f'POLL {len(tasks)} task(s) every {a.every} s')
    res = poll_tasks(tasks, ctx, key, a.every, a.timeout)
    bad = {t: s for t, s in res.items() if s != 'succeeded'}
    if bad:
        raise SystemExit(f'not saved: {bad}')
    return res


# ---------------------------------------------------------------------------------------------------------------------
# estimate, log


def cmd_estimate(a):
    load_env(Path(a.project))
    model = a.model or os.environ.get('ARK_MODEL') or DEFAULT_MODEL
    if a.duration <= 0:
        raise SystemExit('--duration must be the planned output length in seconds')
    has_video = a.input_video_seconds > 0
    print(f'ESTIMATE model {model}, {a.duration:g} s output, {a.aspect}, '
          f'input video {a.input_video_seconds:g} s, x{a.count}   [list price checked {CHECKED}]')
    print(f'  tokens = (input video s + output s) x width x height x {FPS} / 1024'
          + (f'; input video billed as at least {VIDEO_MIN_BILLED_S} s (assumption from vendor examples)'
             if has_video else ''))
    rows = {}
    for res in ((a.resolution,) if a.resolution else RESOLUTIONS):
        w, h = ASPECTS[a.aspect][res]
        tok = est_tokens(a.duration, w, h, a.input_video_seconds)
        cny, note = est_cost(model, res, has_video, tok, a.price)
        rows[res] = cny
        size = f'{w}x{h}'
        if cny is None:
            print(f'  {res:<6} {size:<10} {tok:>10,.0f} tokens   {note}')
        else:
            print(f'  {res:<6} {size:<10} {tok:>10,.0f} tokens  ≈ {cny:7.2f} CNY each, {cny * a.count:8.2f} CNY '
                  f'x{a.count}  ({note})')
    if rows.get('480p') is not None and rows.get('1080p') is not None:
        per = rows['480p'] + rows['1080p']
        print(f'  draft 480p + upgrade 1080p ≈ {per:.2f} CNY per kept shot; a rejected draft costs only '
              f'{rows["480p"]:.2f}. With {a.takes} draft take(s) per kept shot: '
              f'≈ {(rows["480p"] * a.takes + rows["1080p"]) * a.count:.2f} CNY for {a.count} shot(s)')
    print('  Estimates only. The bill follows usage.completion_tokens; check prices and your plan in the console.')
    return rows


def cmd_log(a):
    ctx = make_ctx(a)
    recs = ctx['log'].records()
    if a.shot:
        recs = [r for r in recs if r.get('shot') == a.shot]
    if not recs:
        print('No records in', rel(ctx['log'].path, ctx['project']))
        return
    shown = recs[-a.tail:] if a.tail else recs
    print(f'{"time":<20} {"shot":<12} {"action":<16} {"task":<28} {"res":<6} {"tokens":>9} {"CNY":>8}')
    for r in shown:
        tok = (r.get('usage') or {}).get('completion_tokens')
        cny = r.get('cost_cny_list')
        print(f'{r.get("time", "")[:19]:<20} {str(r.get("shot", "")):<12} {r.get("action", ""):<16} '
              f'{str(r.get("task", r.get("draft_task", ""))):<28} {str(r.get("resolution", "")):<6} '
              f'{(f"{tok:,}" if tok else ""):>9} {(f"{cny:.2f}" if cny is not None else ""):>8}')
    states = {t: s for t, s in ctx['log'].tasks().items()
              if not a.shot or ((s['submit'] or s['final'] or {}).get('shot') == a.shot)}
    done = [s['final'] for s in states.values() if s['final'] and s['final'].get('action') == 'done']
    failed = [s for s in states.values() if s['final'] and s['final'].get('action') != 'done']
    pending = [t for t, s in states.items() if s['submit'] and not s['final']]
    tokens = sum((d.get('usage') or {}).get('completion_tokens') or 0 for d in done)
    spend = sum(d.get('cost_cny_list') or 0 for d in done)
    print(f'\n{len(states)} task(s): {len(done)} done, {len(failed)} failed/expired, {len(pending)} pending'
          + (f' ({", ".join(pending)})' if pending and len(pending) <= 5 else ''))
    print(f'{tokens:,} completion tokens ≈ {spend:.2f} CNY at list price (checked {CHECKED}); '
          'your bill may differ (plans, packs, minimums)')


# ---------------------------------------------------------------------------------------------------------------------
# CLI


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--project', default='.', help='project root; relative paths and gen/ live here (default: .)')
    common.add_argument('--gen-dir', default='gen', help='output folder inside the project (default: gen)')
    common.add_argument('--model', help=f'model id (default: $ARK_MODEL or {DEFAULT_MODEL})')
    common.add_argument('--base-url', help=f'API base (default: $ARK_BASE_URL or {DEFAULT_BASE_URL})')
    waiting = argparse.ArgumentParser(add_help=False)
    waiting.add_argument('--no-wait', action='store_true', help='submit only; collect later with poll --pending')
    waiting.add_argument('--every', type=int, default=15, help='poll interval, seconds (default 15)')
    waiting.add_argument('--timeout', type=float, default=60, help='stop waiting after N minutes (default 60)')
    waiting.add_argument('--format', choices=('mov', 'mp4'), default='mov', help='output container (default mov)')

    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('submit', parents=[common, waiting], help='create one generation task')
    s.add_argument('--shot', required=True, help='shot id; results go to <gen>/<shot>/')
    s.add_argument('--mode', choices=('first', 'first_last', 'ref'), required=True,
                   help='first: first frame; first_last: first + last frame; ref: reference images (+ video URLs)')
    s.add_argument('--first', help='first-frame image (path or URL)')
    s.add_argument('--last', help='last-frame image (path or URL)')
    s.add_argument('--image', action='append', help='reference image (path or URL), repeatable, order = @图片1, 2, ...')
    s.add_argument('--video-url', action='append', help='reference video, public URL or asset://ID, repeatable')
    s.add_argument('--video-seconds', type=float, help='total length of the reference videos (for the cost estimate)')
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('--prompt-file', help='UTF-8 text file with the prompt')
    g.add_argument('--prompt', help='prompt text (prefer --prompt-file: it is reviewable and versioned)')
    s.add_argument('--duration', type=int, required=True, help='seconds (2.5: 4-30, or -1 = model decides)')
    s.add_argument('--resolution', choices=RESOLUTIONS, help='default 1080p; --draft forces 480p')
    s.add_argument('--draft', action='store_true', help='480p draft that can later be upgraded to 1080p unchanged')
    s.add_argument('--aspect', choices=tuple(ASPECTS), default=None,
                   help='output aspect (default: first / first_last follow the first frame, ref mode 16:9); frames '
                        'are normalised to it, ref mode sends it as ratio')
    s.add_argument('--allow-crop', action='store_true',
                   help='centre-crop first/last frames that are more than 5%% off --aspect (refused otherwise)')
    s.add_argument('--crop-refs', action='store_true', help='also crop reference images to --aspect')
    s.add_argument('--task-type', choices=('reference', 'auto'), default='reference',
                   help='ref mode: omni_reference_task_type (default reference: never edit/extend)')
    s.add_argument('--audio', action='store_true', help='let the model generate sound (default off: mix later)')
    s.add_argument('--watermark', action='store_true', help='add the vendor watermark (default off)')
    s.add_argument('--no-last-frame', action='store_true', help='do not ask for the last frame image')
    s.add_argument('--allow-markup', action='store_true', help='send prompts containing () <> {} 【】 anyway')
    s.add_argument('--extra', help='JSON object merged into the body, for parameters this script does not know')
    s.add_argument('--price', type=float, help='CNY per million tokens, overrides the built-in table')
    s.add_argument('--no-probe', action='store_true', help='skip the reachability check of URL inputs')
    s.add_argument('--dry', action='store_true', help='check and print the request; no network call')
    s.set_defaults(func=cmd_submit)

    u = sub.add_parser('upgrade', parents=[common, waiting], help='480p drafts -> 1080p, same content')
    u.add_argument('pairs', nargs='*', metavar='SHOT=DRAFT_TASK_ID')
    u.add_argument('--from-json', help='file with {"SHOT": "DRAFT_TASK_ID", ...} or [{"shot":..,"task":..}]')
    u.add_argument('--again', action='store_true', help='upgrade even if the log shows it was done')
    u.add_argument('--dry', action='store_true', help='print the plan and bodies; no network call')
    u.set_defaults(func=cmd_upgrade)

    p = sub.add_parser('poll', parents=[common], help='wait for tasks and download the results')
    p.add_argument('tasks', nargs='*', metavar='TASK_ID')
    p.add_argument('--pending', action='store_true', help='every task in the log without a result yet')
    p.add_argument('--shot', help='folder for task ids that are not in the log (default: misc)')
    p.add_argument('--every', type=int, default=15)
    p.add_argument('--timeout', type=float, default=60)
    p.set_defaults(func=cmd_poll)

    e = sub.add_parser('estimate', parents=[common], help='cost before you spend')
    e.add_argument('--duration', type=float, required=True, help='output seconds')
    e.add_argument('--resolution', choices=RESOLUTIONS, help='default: show all')
    e.add_argument('--aspect', choices=tuple(ASPECTS), default='16:9')
    e.add_argument('--input-video-seconds', type=float, default=0.0, help='reference video length, if any')
    e.add_argument('--count', type=int, default=1, help='number of shots')
    e.add_argument('--takes', type=int, default=2, help='draft takes per kept shot (default 2)')
    e.add_argument('--price', type=float, help='CNY per million tokens, overrides the table')
    e.set_defaults(func=cmd_estimate)

    lg = sub.add_parser('log', parents=[common], help='tasks, tokens and estimated spend from the log')
    lg.add_argument('--shot')
    lg.add_argument('--tail', type=int, default=30, help='rows to show (0 = all)')
    lg.set_defaults(func=cmd_log)
    return ap


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')     # prompts are often Chinese
    a = parser().parse_args(argv)
    return a.func(a)


if __name__ == '__main__':
    main()
