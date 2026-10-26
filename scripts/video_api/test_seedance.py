# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31",
#     "pillow>=10.1",
# ]
# ///
"""Offline tests for seedance.py: request building, redaction, prompt checks, cost formula, and the submit -> poll ->
upgrade -> log cycle against a fake server. Uses a fake key; never touches the network and spends nothing.

uv run test_seedance.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seedance as S  # noqa: E402

FAKE_KEY = 'sk-FAKE-0123456789abcdef'
SIGNED_URL = 'https://storage.example.com/ref/blockout.mp4?X-Tos-Signature=SECRETSIG&X-Tos-Expires=3600'


class FakeResponse:
    def __init__(self, status=200, data=None, body=b'', headers=None):
        self.status_code, self._data, self._body = status, data, body
        self.headers = headers or {}
        self.text = json.dumps(data) if data is not None else body.decode('latin1')

    def json(self):
        return self._data

    def iter_content(self, n):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """Scripted replies. Records every call so tests can assert on what would have been sent."""

    def __init__(self, posts=(), gets=()):
        self.posts, self.gets, self.calls = list(posts), list(gets), []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, headers, json))
        item = (self.posts if method == 'POST' else self.gets).pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, stream=False, timeout=None, headers=None):      # result downloads and probes
        self.calls.append(('DOWNLOAD', url, headers, None))
        return FakeResponse(200, body=b'fake-bytes-' + url.encode()[-12:])


def no_network(*a, **k):
    raise AssertionError('network call in an offline test')


@contextlib.contextmanager
def patched(server=None):
    old = (requests.request, requests.get, S.time.sleep)
    requests.request = server.request if server else no_network
    requests.get = server.get if server else no_network
    S.time.sleep = lambda s: None
    try:
        yield
    finally:
        requests.request, requests.get, S.time.sleep = old


def make_project(tmp):
    p = Path(tmp)
    (p / 'kf').mkdir()
    (p / 'prompts').mkdir()
    Image.new('RGB', (2560, 1440), (200, 120, 40)).save(p / 'kf' / 'a.png')       # 16:9, shrunk to 1920x1080
    Image.new('RGBA', (2560, 1440), (20, 120, 240, 128)).save(p / 'kf' / 'b.png')
    Image.new('RGB', (2400, 1600), (60, 160, 90)).save(p / 'kf' / 'wide.png')     # 3:2: no supported aspect
    Image.new('RGB', (1080, 1920), (90, 60, 160)).save(p / 'kf' / 'tall1.png')    # 9:16 keyframes
    Image.new('RGB', (1080, 1920), (160, 60, 90)).save(p / 'kf' / 'tall2.png')
    Image.new('RGB', (1200, 1800), (90, 90, 90)).save(p / 'kf' / 'ref1.png')      # portrait reference
    Image.new('RGB', (1600, 1600), (10, 10, 10)).save(p / 'kf' / 'ref2.png')
    (p / 'prompts' / 'S01.txt').write_text('镜头缓慢推进，产品放在木桌中央，晨光从左侧照进来。', encoding='utf-8')
    (p / 'prompts' / 'R01.txt').write_text('@图片1 是产品外观，@图片2 是场景。参考@视频1的运镜，产品保持不变。',
                                           encoding='utf-8')
    (p / '.env').write_text(f'ARK_API_KEY={FAKE_KEY}\n', encoding='utf-8')
    return p


def run(argv):
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            S.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            print(e.code)
    return code, out.getvalue()


# ---------------------------------------------------------------------------------------------------------------------


def test_cost_formula_matches_vendor_examples():
    # vendor price examples (2026-10-15): 16:9, 5 s, no input video: 480p 3.36, 720p 7.56, 1080p 18.71 CNY
    for res, want in (('480p', 3.36), ('720p', 7.56), ('1080p', 18.71)):
        w, h = S.ASPECTS['16:9'][res]
        cny, _ = S.est_cost(S.DEFAULT_MODEL, res, False, S.est_tokens(5, w, h))
        assert round(cny, 2) == want, (res, cny)
    # with input video, 480p: 2-4 s input -> 3.63, 30 s input -> 14.12
    w, h = S.ASPECTS['16:9']['480p']
    assert round(S.est_cost(S.DEFAULT_MODEL, '480p', True, S.est_tokens(5, w, h, 2))[0], 2) == 3.63
    assert round(S.est_cost(S.DEFAULT_MODEL, '480p', True, S.est_tokens(5, w, h, 30))[0], 2) == 14.12
    assert S.est_cost('some-other-model', '1080p', False, 1e6)[0] is None
    assert S.est_cost('some-other-model', '1080p', False, 1e6, price=10)[0] == 10


def test_dry_first_last_builds_and_hides_secrets():
    with tempfile.TemporaryDirectory() as tmp, patched():
        p = make_project(tmp)
        os.environ.pop('ARK_API_KEY', None)
        code, out = run(['submit', '--project', str(p), '--shot', 'S01', '--mode', 'first_last', '--first', 'kf/a.png',
                         '--last', 'kf/b.png', '--prompt-file', 'prompts/S01.txt', '--duration', '5', '--draft', '--dry'])
        assert code == 0, out
        assert FAKE_KEY not in out and 'base64,' not in out
        assert '"draft": true' in out and '"resolution": "480p"' in out and '"ratio": "adaptive"' in out
        assert 'omni_reference_task_type' not in out
        sent = sorted((p / 'gen' / 'S01' / 'sent').glob('*.jpg'))
        assert [Image.open(f).size for f in sent] == [(1920, 1080), (1920, 1080)]
        assert not (p / 'gen' / 'video_log.jsonl').exists(), 'a dry run must not log an API call'
        os.environ.pop('ARK_API_KEY', None)


def test_aspect_follows_frames_and_crops_are_refused():
    with tempfile.TemporaryDirectory() as tmp, patched():
        p = make_project(tmp)
        base = ['submit', '--project', str(p), '--shot', 'V1', '--mode', 'first_last', '--first', 'kf/tall1.png',
                '--last', 'kf/tall2.png', '--prompt-file', 'prompts/S01.txt', '--duration', '5', '--draft', '--dry']
        code, out = run(base)                       # no --aspect: a vertical first frame makes a vertical task
        assert code == 0, out
        assert 'frames normalised to 9:16, from first frame 1080x1920' in out, out
        sent = sorted((p / 'gen' / 'V1' / 'sent').glob('*.jpg'))
        assert [Image.open(f).size for f in sent] == [(1080, 1920), (1080, 1920)]
        assert 'later upgrade 1080p: 243,000 tokens' in out, out                  # 1080x1920, 5 s
        code, out = run(base + ['--aspect', '16:9'])  # the old default: would crop a 1080x608 strip
        assert code == 1 and 'is 216% off --aspect 16:9' in out and '--allow-crop' in out, out
        code, out = run(base + ['--aspect', '16:9', '--allow-crop'])
        assert code == 0 and 'centre-cropped 1080x1920 -> 1080x608' in out, out
        code, out = run(['submit', '--project', str(p), '--shot', 'W1', '--mode', 'first', '--first', 'kf/wide.png',
                         '--prompt-file', 'prompts/S01.txt', '--duration', '5', '--dry'])
        assert code == 1 and 'no supported aspect' in out, out
        code, out = run(['submit', '--project', str(p), '--shot', 'W1', '--mode', 'first', '--first', 'kf/wide.png',
                         '--prompt-file', 'prompts/S01.txt', '--duration', '5', '--dry', '--aspect', '4:3'])
        assert code == 1 and '12% off --aspect 4:3' in out, out


def test_ref_body_roles_ratio_and_url_redaction():
    with tempfile.TemporaryDirectory() as tmp, patched():
        p = make_project(tmp)
        a = S.parser().parse_args(['submit', '--project', str(p), '--shot', 'R01', '--mode', 'ref', '--image',
                                   'kf/ref1.png', '--image', 'kf/ref2.png', '--video-url', SIGNED_URL,
                                   '--prompt-file', 'prompts/R01.txt', '--duration', '8', '--aspect', '9:16'])
        ctx = S.make_ctx(a)
        body, assets, _, problems, warns = S.plan_submit(a, ctx)
        assert not problems, problems
        assert body['ratio'] == '9:16' and body['omni_reference_task_type'] == 'reference'
        assert body['resolution'] == '1080p' and 'draft' not in body
        roles = [c.get('role') for c in body['content']]
        assert roles == [None, 'reference_image', 'reference_image', 'reference_video']
        assert body['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        assert assets[0]['size'] == [1080, 1620], assets[0]['size']       # portrait: fit 1080x1920, not cropped
        assert body['content'][3]['video_url']['url'] == SIGNED_URL         # sent untouched ...
        shown = json.dumps(S.redacted(body, assets))
        assert 'SECRETSIG' not in shown and '?<query hidden>' in shown       # ... but never printed
        assert 'base64,' not in shown


def test_prompt_checks():
    probs, _ = S.prompt_checks('【字幕】产品出现', 'first', 0, 0)
    assert probs and 'subtitles' in probs[0]
    probs, warns = S.prompt_checks('【字幕】产品出现', 'first', 0, 0, allow_markup=True)
    assert not probs and warns
    probs, _ = S.prompt_checks('@图片1 和 @图片3', 'ref', 2, 0)
    assert any('#3' in x for x in probs)
    probs, warns = S.prompt_checks('@image 1 is the product, @Image 2 the room, camera from @video 1', 'ref', 2, 1)
    assert not probs and not any('never named' in w for w in warns), (probs, warns)
    _, warns = S.prompt_checks('以@图片1至@图片3的顺序，生成一段 5 秒视频', 'ref', 3, 0)
    assert not any('never named' in w for w in warns) and any('duration' in w for w in warns)
    _, warns = S.prompt_checks('把@视频1里的杯子替换成产品', 'ref', 0, 1)
    assert any('edit/extend' in w for w in warns)


def test_refusals():
    with tempfile.TemporaryDirectory() as tmp, patched():
        p = make_project(tmp)
        base = ['submit', '--project', str(p), '--shot', 'X', '--prompt-file', 'prompts/S01.txt', '--dry']
        code, out = run(base + ['--mode', 'first_last', '--first', 'kf/a.png', '--duration', '5'])
        assert code == 1 and 'exactly --first IMAGE and --last IMAGE' in out
        code, out = run(base + ['--mode', 'first', '--first', 'kf/a.png', '--duration', '3'])
        assert code == 1 and 'duration 3 s' in out
        code, out = run(base + ['--mode', 'ref', '--image', 'kf/a.png', '--video-url', 'kf/clip.mp4', '--duration', '5'])
        assert code == 1 and 'public http(s) URL' in out
        code, out = run(base + ['--mode', 'first', '--first', 'kf/missing.png', '--duration', '5'])
        assert code == 1 and 'file not found' in out
        code, out = run(base + ['--mode', 'first', '--first', 'kf/a.png', '--duration', '5', '--resolution', '1080p',
                                '--draft'])
        assert code == 1 and 'always 480p' in out


def test_upgrade_body_sends_only_allowed_fields():
    b = S.upgrade_body('cgt-123', S.DEFAULT_MODEL)
    assert set(b) == {'model', 'content', 'resolution', 'return_last_frame', 'output_format'}
    assert b['content'] == [{'type': 'draft_task', 'draft_task': {'id': 'cgt-123'}}] and b['resolution'] == '1080p'


def test_submit_poll_upgrade_log_cycle_with_fake_server():
    with tempfile.TemporaryDirectory() as tmp:
        p = make_project(tmp)
        os.environ.pop('ARK_API_KEY', None)
        done = {'id': 'cgt-1', 'status': 'succeeded', 'model': S.DEFAULT_MODEL, 'resolution': '480p', 'ratio': '16:9',
                'duration': 5, 'output_format': 'mov', 'usage': {'completion_tokens': 48038},
                'content': {'video_url': 'https://cdn.example.com/v.mov?sig=1', 'last_frame_url': 'https://cdn.example.com/l.jpg?sig=2'}}
        srv = FakeServer(posts=[FakeResponse(429, {'error': 'busy'}, headers={'Retry-After': '0'}),
                                FakeResponse(200, {'id': 'cgt-1'})],
                         gets=[FakeResponse(503, {'error': 'x'}), FakeResponse(200, {'id': 'cgt-1', 'status': 'running'}),
                               FakeResponse(200, done)])
        with patched(srv):
            code, out = run(['submit', '--project', str(p), '--shot', 'S01', '--mode', 'first_last', '--first',
                             'kf/a.png', '--last', 'kf/b.png', '--prompt-file', 'prompts/S01.txt', '--duration', '5',
                             '--draft', '--every', '0'])
        assert code == 0, out
        posts = [c for c in srv.calls if c[0] == 'POST']
        assert len(posts) == 2 and posts[1][2]['Authorization'] == f'Bearer {FAKE_KEY}'
        assert (p / 'gen' / 'S01' / 'cgt-1.mov').is_file() and (p / 'gen' / 'S01' / 'cgt-1.last.jpg').is_file()
        log_text = (p / 'gen' / 'video_log.jsonl').read_text(encoding='utf-8')
        assert FAKE_KEY not in log_text and 'base64' not in log_text and 'sig=' not in log_text
        assert FAKE_KEY not in out
        recs = [json.loads(x) for x in log_text.splitlines()]
        assert [r['action'] for r in recs] == ['submit', 'done']
        assert recs[1]['cost_cny_list'] == 3.36 and recs[0]['inputs'][0]['source'] == 'kf/a.png'

        with patched(FakeServer()):
            code, out = run(['poll', '--project', str(p), '--pending'])
        assert 'No pending tasks' in out

        # upgrade: one POST, then a rerun must not pay again
        hd = dict(done, id='cgt-2', resolution='1080p', usage={'completion_tokens': 243000},
                  content={'video_url': 'https://cdn.example.com/h.mov?sig=3'})
        srv = FakeServer(posts=[FakeResponse(200, {'id': 'cgt-2'})], gets=[FakeResponse(200, hd)])
        with patched(srv):
            code, out = run(['upgrade', '--project', str(p), 'S01=cgt-1', '--every', '0'])
        assert code == 0, out
        assert srv.calls[0][3] == S.upgrade_body('cgt-1', S.DEFAULT_MODEL)
        assert (p / 'gen' / 'S01' / 'cgt-2.mov').is_file()
        srv = FakeServer()
        with patched(srv):
            code, out = run(['upgrade', '--project', str(p), 'S01=cgt-1'])
        assert code == 0 and not [c for c in srv.calls if c[0] == 'POST'] and 'already upgraded' in out

        with patched():
            code, out = run(['log', '--project', str(p)])
        assert '2 task(s): 2 done' in out and '22.07 CNY' in out, out


def test_uncertain_submit_is_not_resent():
    with tempfile.TemporaryDirectory() as tmp:
        p = make_project(tmp)
        os.environ.pop('ARK_API_KEY', None)
        srv = FakeServer(posts=[requests.ReadTimeout('read timed out')])
        with patched(srv):
            code, out = run(['submit', '--project', str(p), '--shot', 'S01', '--mode', 'first', '--first', 'kf/a.png',
                             '--prompt-file', 'prompts/S01.txt', '--duration', '5', '--no-wait'])
        assert code == 1 and 'may exist' in out
        assert len([c for c in srv.calls if c[0] == 'POST']) == 1
        recs = [json.loads(x) for x in (p / 'gen' / 'video_log.jsonl').read_text(encoding='utf-8').splitlines()]
        assert recs[-1]['action'] == 'submit_uncertain'


def test_missing_key_message():
    with tempfile.TemporaryDirectory() as tmp, patched():
        p = make_project(tmp)
        (p / '.env').unlink()
        os.environ.pop('ARK_API_KEY', None)
        code, out = run(['poll', '--project', str(p), 'cgt-9'])
        assert code == 1 and 'ARK_API_KEY is not set' in out


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
