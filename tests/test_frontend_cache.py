from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.frontend import register_frontend
from backend.api.middleware import register_middleware


class FrontendCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        (self.root / 'index.html').write_bytes(
            b'<link href="/style.css">\n<script src="/app.js?x=1&amp;v=old#start"></script>'
            b'<script src="//cdn.example/dist.js"></script>'
        )
        (self.root / 'app.js').write_bytes(b'const x = 1;')
        (self.root / 'style.css').write_bytes(b'body { mask: url(/icon.svg); }')
        (self.root / 'icon.svg').write_bytes(b'<svg>A</svg>')

        app = FastAPI()
        register_middleware(app)

        @app.get('/auth/providers')
        def providers():
            return {'ok': True}

        with patch('backend.api.frontend.FRONTEND_DIR', self.root):
            register_frontend(app)
        self.client = self.enterContext(TestClient(app))

    def test_html_and_versioned_assets_revalidate(self):
        initial = self.client.get('/')
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.headers['cache-control'], 'no-cache')
        self.assertIn('//cdn.example/dist.js', initial.text)
        version = sha256((self.root / 'app.js').read_bytes()).hexdigest()
        self.assertIn(f'/app.js?x=1&amp;v={version}#start', initial.text)

        etag = initial.headers['etag']
        result = self.client.get('/try/', headers={'If-None-Match': f'"old", W/{etag}'})
        self.assertEqual(result.status_code, 304)
        self.assertEqual(result.headers['etag'], etag)
        self.assertEqual(result.headers['cache-control'], 'no-cache')
        self.assertEqual(result.content, b'')

        url = f'/app.js?v={version}'
        asset = self.client.get(url)
        self.assertEqual(asset.content, b'const x = 1;')
        self.assertEqual(asset.headers['cache-control'], 'public, max-age=31536000, immutable')
        not_modified = self.client.get(url, headers={'If-None-Match': asset.headers['etag']})
        self.assertEqual(not_modified.status_code, 304)
        self.assertEqual(not_modified.headers['cache-control'], asset.headers['cache-control'])

    def test_modifying_css_dependency_changes_css_url_and_html_etag(self):
        initial = self.client.get('/')
        old_css_url = re.search(r'href="([^"]+)"', initial.text)[1]
        old_css = self.client.get(old_css_url)
        old_icon_url = re.search(r'url\(([^)]+)\)', old_css.text)[1]
        self.assertIn('immutable', old_css.headers['cache-control'])
        self.assertIn('immutable', self.client.get(old_icon_url).headers['cache-control'])
        (self.root / 'icon.svg').write_bytes(b'<svg>B changed</svg>')

        updated = self.client.get('/', headers={'If-None-Match': initial.headers['etag']})
        new_css_url = re.search(r'href="([^"]+)"', updated.text)[1]
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(new_css_url, old_css_url)
        self.assertNotEqual(updated.headers['etag'], initial.headers['etag'])
        self.assertEqual(self.client.get(old_css_url).headers['cache-control'], 'no-store')
        new_icon_url = re.search(r'url\(([^)]+)\)', self.client.get(new_css_url).text)[1]
        self.assertEqual(self.client.get(new_icon_url).content, b'<svg>B changed</svg>')

    def test_only_matching_static_content_is_immutable(self):
        js = (self.root / 'app.js').read_bytes()
        version = sha256(js).hexdigest()
        for url in ('/app.js', '/app.js?v=old', f'/app.js?v={version}&v={version}'):
            with self.subTest(url=url):
                result = self.client.get(url)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.content, js)
                self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/app.js', headers={'Range': 'bytes=0-4'}).content, b'const')

        missing = self.client.get(f'/missing.js?v={version}')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers['cache-control'], 'no-store')
        api = self.client.get(f'/auth/providers?v={version}')
        self.assertEqual(api.json(), {'ok': True})
        self.assertNotIn('cache-control', api.headers)
