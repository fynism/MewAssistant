import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.auth import create_access_token, get_db
from backend.database import Base
from backend.models import PersonalApiKey, User
from backend.services.api_keys import authenticate_key


class ApiKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.app import app
        cls.app = app

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'db.sqlite'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def db_override():
            with self.factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = db_override
        self.client = TestClient(self.app)
        with self.factory() as db:
            db.add_all([User(id=1, username="alice", password_hash="x", role="user"),
                        User(id=2, username="bob", password_hash="x", role="user")])
            db.commit()
        self.alice = {"Authorization": "Bearer " + create_access_token("alice", "user")}
        self.bob = {"Authorization": "Bearer " + create_access_token("bob", "user")}

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def test_create_list_revoke_and_isolation(self):
        endpoint = "/account/api-keys"
        self.assertEqual(self.client.post(endpoint, json={"name": "Codex"}).status_code, 401)
        self.assertEqual(self.client.post(endpoint, headers=self.alice,
            json={"name": "   "}).status_code, 422)
        created = self.client.post(endpoint, headers=self.alice, json={"name": " Codex "})
        self.assertEqual(created.status_code, 201, created.text)
        key = created.json()["key"]
        key_id = created.json()["id"]
        self.assertEqual(created.json()["name"], "Codex")
        with self.factory() as db:
            stored = db.get(PersonalApiKey, key_id)
            self.assertNotEqual(stored.token_digest, key)
            self.assertNotIn(key, repr(stored.__dict__))
            self.assertEqual(authenticate_key(db, key), 1)
            self.assertIsNone(authenticate_key(db, key + "wrong"))
        listed = self.client.get(endpoint, headers=self.alice).json()["items"]
        self.assertEqual(len(listed), 1)
        self.assertNotIn("key", listed[0])
        self.assertIsNotNone(listed[0]["lastUsedAt"])
        self.assertEqual(self.client.get(endpoint, headers=self.bob).json()["items"], [])
        self.assertEqual(self.client.delete(f"{endpoint}/{key_id}", headers=self.bob).status_code, 404)
        self.assertEqual(self.client.delete(f"{endpoint}/{key_id}", headers=self.alice).status_code, 200)
        self.assertEqual(self.client.delete(f"{endpoint}/{key_id}", headers=self.alice).status_code, 200)
        with self.factory() as db:
            self.assertIsNone(authenticate_key(db, key))
        self.assertEqual(self.client.get(endpoint, headers={"Authorization": "Bearer " + key}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
