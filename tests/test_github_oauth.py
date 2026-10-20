"""Exercise real HTTP routes and account storage without GitHub or Redis access."""

import base64
import hashlib
import json
import threading
import time
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import redis
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import get_db, get_password_hash
from backend.core.config import settings
from backend.database import Base
from backend.models import User
from backend.routers import auth, github_auth
from backend.services import github_oauth as oauth

ORIGIN = "https://platform.example"


class ExpiringRedis:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def setex(self, key, ttl, value):
        with self.lock:
            self.values[key] = (time.monotonic() + ttl, value)

    def getdel(self, key):
        with self.lock:
            expiry, value = self.values.pop(key, (0, None))
            return value if expiry > time.monotonic() else None


class GitHubOAuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.app = FastAPI()
        self.app.include_router(auth.router)
        self.app.include_router(github_auth.router)

        def db_dependency():
            with self.factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = db_dependency
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)
        self.config = replace(settings, github_oauth_enabled=True, github_client_id="fixture-client",
                              github_client_secret="fixture-secret", github_redirect_uri=ORIGIN + "/auth/github/callback")
        self.store = ExpiringRedis()
        for target in (oauth, github_auth):
            patcher = patch.object(target, "settings", self.config)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(oauth, "oauth_redis", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.github = Mock()
        self.github.__enter__ = Mock(return_value=self.github)
        self.github.__exit__ = Mock(return_value=False)
        patcher = patch.object(oauth.requests, "Session", return_value=self.github)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profile()

    def profile(self, id=123, login="octocat"):
        self.github.post.return_value = Mock(status_code=200, json=lambda: {"access_token": "github-secret-token"})
        self.github.get.return_value = Mock(status_code=200, json=lambda: {"id": id, "login": login, "email": None})

    def start(self, next_path="/account", client=None):
        client = client or self.client
        response = client.post("/auth/github/start", json={"next": next_path}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200, response.text)
        return parse_qs(urlsplit(response.json()["authorization_url"]).query), response

    def callback(self, state, client=None, **extra):
        return (client or self.client).get("/auth/github/callback", params={"state": state, "code": "fixture-code", **extra},
                                           follow_redirects=False)

    def complete(self, client=None):
        return (client or self.client).post("/auth/github/complete", json={}, headers={"Origin": ORIGIN})

    def login(self):
        query, _ = self.start()
        response = self.callback(query["state"][0])
        self.assertIn("github=success", response.headers["location"])
        response = self.complete()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_disabled_provider_leaves_password_login_available(self):
        with patch.object(github_auth, "settings", replace(self.config, github_oauth_enabled=False)):
            self.assertEqual(self.client.get("/auth/providers").json(), {"github": False})
            self.assertEqual(self.client.post("/auth/github/start", json={}).status_code, 503)
            self.assertEqual(self.client.get("/auth/github/callback").status_code, 503)
            self.assertEqual(self.complete().status_code, 503)
        with self.factory() as db:
            db.add(User(username="legacy", password_hash=get_password_hash("secret"), role="user"))
            db.commit()
        response = self.client.post("/auth/login", json={"username": "legacy", "password": "secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/auth/register", json={"username": "new", "password": "secret"}).status_code, 422)

    def test_start_has_pkce_secure_cookies_and_no_private_scopes(self):
        self.assertEqual(self.client.get("/auth/providers").json(), {"github": True})
        query, response = self.start()
        self.assertEqual(query["client_id"], ["fixture-client"])
        self.assertNotIn("fixture-secret", response.text)
        self.assertNotIn("repo", query.get("scope", [""])[0])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        cookie = response.headers["set-cookie"]
        for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/auth/github"):
            self.assertIn(attribute, cookie)
        state_payload = json.loads(next(iter(self.store.values.values()))[1])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(state_payload["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_first_login_creates_ordinary_account_and_one_use_ticket(self):
        query, _ = self.start("/try?knowledge=kb&github_error=forged")
        response = self.callback(query["state"][0])
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], ORIGIN + "/try?knowledge=kb&github=success")
        self.assertNotIn("github-secret-token", str(response.headers))
        self.assertNotIn("access_token", response.headers["location"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        data = self.complete().json()
        self.assertEqual(data["role"], "user")
        self.assertEqual(self.complete().status_code, 401)
        me = self.client.get("/auth/me", headers={"Authorization": "Bearer " + data["access_token"]})
        self.assertEqual(me.json()["username"], data["username"])
        with self.factory() as db:
            user = db.query(User).one()
            self.assertEqual(user.github_id, "123")
            self.assertEqual(user.password_hash, "!")
        self.assertEqual(self.client.post("/auth/login", json={"username": data["username"], "password": "!"}).status_code, 401)
        self.callback(query["state"][0])
        self.github.post.assert_called_once()
        self.assertTrue(self.github.post.call_args.kwargs["data"]["code_verifier"])

    def test_renamed_github_account_keeps_same_local_account(self):
        first = self.login()
        self.profile(login="renamed")
        second = self.login()
        self.assertEqual(first["username"], second["username"])
        with self.factory() as db:
            self.assertEqual(db.query(User).count(), 1)

    def test_same_username_cannot_take_over_existing_admin(self):
        with self.factory() as db:
            db.add(User(username="gh_octocat_123", password_hash="existing", role="admin"))
            db.commit()
        data = self.login()
        self.assertNotEqual(data["username"], "gh_octocat_123")
        self.assertEqual(data["role"], "user")
        with self.factory() as db:
            self.assertEqual(db.query(User).filter(User.username == "gh_octocat_123").one().github_id, None)
        self.profile(id=456)
        other = self.login()
        self.assertNotEqual(data["username"], other["username"])

    def test_state_and_ticket_are_bound_to_original_browser(self):
        query, _ = self.start()
        with TestClient(self.app, base_url=ORIGIN) as stranger:
            stranger.cookies.set(github_auth.BROWSER_COOKIE, "different-browser", path=github_auth.COOKIE_PATH)
            response = self.callback(query["state"][0], client=stranger)
            self.assertIn("github_error=expired", response.headers["location"])
            self.github.post.assert_not_called()
        response = self.callback(query["state"][0])
        self.assertIn("github=success", response.headers["location"])
        with TestClient(self.app, base_url=ORIGIN) as stranger:
            stranger.cookies.set(github_auth.TICKET_COOKIE, self.client.cookies.get(github_auth.TICKET_COOKIE), path=github_auth.COOKIE_PATH)
            self.assertEqual(self.complete(stranger).status_code, 401)
        self.assertEqual(self.complete().status_code, 200)

    def test_cross_origin_posts_are_rejected_without_consuming_ticket(self):
        for origin in ("https://evil.example", "null", ""):
            self.assertEqual(self.client.post("/auth/github/start", json={}, headers={"Origin": origin}).status_code, 403)
        query, _ = self.start()
        self.callback(query["state"][0])
        self.assertEqual(self.client.post("/auth/github/complete", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.complete().status_code, 200)

    def test_tampered_state_does_not_consume_real_flow_and_missing_code_fails(self):
        query, _ = self.start()
        self.assertIn("github_error=expired", self.callback("tampered").headers["location"])
        # The error response clears this browser's cookie; restore the original
        # binding to show the unrelated state was not consumed in Redis.
        self.github.post.assert_not_called()
        self.assertEqual(len(self.store.values), 1)
        query, _ = self.start()
        response = self.client.get("/auth/github/callback", params={"state": query["state"][0]}, follow_redirects=False)
        self.assertIn("github_error=failed", response.headers["location"])
        self.github.post.assert_not_called()

    def test_concurrent_first_logins_share_one_account(self):
        # Separate real transactions reach their first commit together. The losing
        # unique-ID insert must roll back and return the winner's account.
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'race.sqlite'}", connect_args={"timeout": 10})
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            barrier = threading.Barrier(2)

            def register():
                with factory() as db:
                    event.listen(db, "before_commit", lambda _: barrier.wait(timeout=5), once=True)
                    return oauth.github_user(db, {"id": 123, "login": "octocat"}).id

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    a, b = list(executor.map(lambda _: register(), range(2)))
                self.assertEqual(a, b)
                with factory() as db:
                    self.assertEqual(db.query(User).count(), 1)
            finally:
                engine.dispose()

    def test_denial_and_expiry_do_not_register_or_call_github(self):
        query, _ = self.start()
        response = self.callback(query["state"][0], error="access_denied", error_description="untrusted-provider-text")
        self.assertIn("github_error=cancelled", response.headers["location"])
        self.assertNotIn("untrusted-provider-text", str(response.headers))
        query, _ = self.start()
        with patch.object(time, "monotonic", return_value=time.monotonic() + oauth.FLOW_TTL + 1):
            self.assertIn("github_error=expired", self.callback(query["state"][0]).headers["location"])
        self.github.post.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(User).count(), 0)

    def test_ticket_expires_and_is_deleted_with_account(self):
        query, _ = self.start()
        self.callback(query["state"][0])
        with patch.object(time, "monotonic", return_value=time.monotonic() + oauth.TICKET_TTL + 1):
            self.assertEqual(self.complete().status_code, 401)
        query, _ = self.start()
        self.callback(query["state"][0])
        with self.factory() as db:
            db.query(User).delete()
            db.commit()
        self.assertEqual(self.complete().status_code, 401)

    def test_network_and_provider_failures_do_not_issue_login(self):
        cases = [requests.Timeout("never expose provider credentials"),
                 Mock(status_code=200, json=lambda: {"error": "bad_verification_code"}),
                 Mock(status_code=302), Mock(status_code=200, json=lambda: [])]
        for result in cases:
            with self.subTest(result=type(result).__name__):
                query, _ = self.start()
                self.github.post.side_effect = result if isinstance(result, Exception) else None
                self.github.post.return_value = result
                response = self.callback(query["state"][0])
                self.assertIn("github_error=", response.headers["location"])
                self.assertNotIn("never expose", str(response.headers))
                self.assertEqual(self.complete().status_code, 401)
        with self.factory() as db:
            self.assertEqual(db.query(User).count(), 0)

    def test_malformed_or_unauthorized_profile_is_rejected(self):
        for profile in ({"id": True, "login": "cat"}, {"id": -1, "login": "cat"},
                        {"id": 123, "login": "<script>"}, [], {"login": "cat"}):
            with self.subTest(profile=profile):
                query, _ = self.start()
                self.github.get.return_value = Mock(status_code=200, json=lambda: profile)
                self.assertIn("github_error=failed", self.callback(query["state"][0]).headers["location"])
        self.profile()
        query, _ = self.start()
        self.github.get.return_value = Mock(status_code=401)
        self.assertIn("github_error=failed", self.callback(query["state"][0]).headers["location"])

    def test_redis_failure_fails_closed_at_start_and_callback(self):
        with patch.object(self.store, "setex", side_effect=redis.ConnectionError("secret endpoint")):
            response = self.client.post("/auth/github/start", json={}, headers={"Origin": ORIGIN})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret endpoint", response.text)
        query, _ = self.start()
        with patch.object(self.store, "getdel", side_effect=redis.ConnectionError()):
            self.assertIn("github_error=unavailable", self.callback(query["state"][0]).headers["location"])
        self.github.post.assert_not_called()
        query, _ = self.start()
        self.callback(query["state"][0])
        with patch.object(self.store, "getdel", side_effect=redis.ConnectionError()):
            self.assertEqual(self.complete().status_code, 503)

    def test_redirects_are_same_origin_and_allowlisted(self):
        for path in ("https://evil.example", "//evil.example/account", "/auth/github/callback", "/\\evil.example", "/\nevil", "//["):
            with self.subTest(path=path):
                query, _ = self.start(path)
                self.assertEqual(self.callback(query["state"][0]).headers["location"], ORIGIN + "/?github=success")

    def test_production_config_requires_https_and_complete_credentials(self):
        for changes in ({"github_client_secret": ""}, {"github_redirect_uri": "http://platform.example/auth/github/callback"},
                        {"github_redirect_uri": ORIGIN + "/wrong"}, {"github_redirect_uri": ORIGIN + "/auth/github/callback?x=1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, **changes)
        replace(self.config, github_redirect_uri="http://127.0.0.1:8000/auth/github/callback")


if __name__ == "__main__":
    unittest.main()
