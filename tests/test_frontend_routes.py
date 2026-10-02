"""Verify that the real application ships the frontend and all of its assets."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import unittest

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'script' and 'src' in values:
            self.paths.append(values['src'])
        if tag == 'link' and 'href' in values:
            self.paths.append(values['href'])


class FrontendRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.app import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_frontend_routes_and_assets(self):
        parser = Assets()
        parser.feed((ROOT / 'frontend/index.html').read_text(encoding='utf-8'))
        for route in ['/', '/services/knowledge', '/workspace/knowledges', '/try', '/account', '/admin']:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIn('MeowConnectPlatform', response.text)
                self.assertIn('connection-example', response.text)
                self.assertIn('no-store', response.headers['cache-control'])
        for asset in parser.paths:
            with self.subTest(asset=asset):
                self.assertTrue(asset.startswith('/'), 'Runtime assets must be served locally')
                local_path = ROOT / 'frontend' / urlsplit(asset).path.lstrip('/')
                self.assertTrue(local_path.is_file())
                response = self.client.get(asset)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, local_path.read_bytes())

    def test_production_page_has_no_demo_controls(self):
        html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
        for retired_copy in ['外部 MCP 接入尚未发布', '原型中模拟', '演示身份', '载入示例资料', '演示凭证', '占位地址']:
            self.assertNotIn(retired_copy, html)


if __name__ == '__main__':
    unittest.main()
