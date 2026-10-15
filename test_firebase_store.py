import unittest
from typing import Any

from google.api_core.exceptions import AlreadyExists

from firebase_store import (
    COLLECTION_NAME,
    LEGACY_OWNER_COLLECTION,
    LEGACY_OWNER_DOCUMENT,
    TransactionData,
    create_transaction,
    delete_transaction,
    ensure_legacy_data_owner,
    list_transactions,
    update_transaction,
    update_transaction_category,
)


class FakeSnapshot:
    def __init__(
        self,
        document_id: str,
        data: dict[str, Any] | None,
        reference: "FakeDocument",
    ) -> None:
        self.id = document_id
        self._data = data
        self.reference = reference
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class FakeDocument:
    def __init__(self, collection: "FakeCollection", document_id: str) -> None:
        self._collection = collection
        self.id = document_id

    @property
    def reference(self) -> "FakeDocument":
        return self

    def create(self, data: dict[str, Any]) -> None:
        if self.id in self._collection.documents:
            raise AlreadyExists("document already exists")
        self._collection.documents[self.id] = data

    def set(self, data: dict[str, Any]) -> None:
        self._collection.documents[self.id] = data

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(
            self.id,
            self._collection.documents.get(self.id),
            self,
        )

    def update(self, data: dict[str, Any]) -> None:
        self._collection.documents[self.id].update(data)

    def delete(self) -> None:
        del self._collection.documents[self.id]


class FakeQuery:
    def __init__(self, collection: "FakeCollection", field_filter: Any) -> None:
        self._collection = collection
        self._field_filter = field_filter

    def stream(self) -> list[FakeSnapshot]:
        return [
            snapshot
            for snapshot in self._collection.stream()
            if snapshot.to_dict().get(self._field_filter.field_path)
            == self._field_filter.value
        ]


class FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    def document(self, document_id: str | None = None) -> FakeDocument:
        if document_id is None:
            document_id = str(self._next_id)
            self._next_id += 1
        return FakeDocument(self, document_id)

    def stream(self) -> list[FakeSnapshot]:
        return [
            FakeSnapshot(document_id, data, self.document(document_id))
            for document_id, data in self.documents.items()
        ]

    def where(self, *, filter: Any) -> FakeQuery:
        return FakeQuery(self, filter)


class FakeBatch:
    def __init__(self) -> None:
        self.updates: list[tuple[FakeDocument, dict[str, Any]]] = []

    def update(self, document: FakeDocument, data: dict[str, Any]) -> None:
        self.updates.append((document, data))

    def commit(self) -> None:
        for document, data in self.updates:
            document.update(data)


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}
        self.collection_instance = self.collection(COLLECTION_NAME)

    def collection(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

    def batch(self) -> FakeBatch:
        return FakeBatch()


class TransactionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.transaction: TransactionData = {
            "type": "Despesa",
            "description": "Mercado",
            "category": "Alimentação",
            "amount_cents": 2599,
            "date": "2026-10-14",
            "time": "12:30",
        }

    def test_create_list_update_and_delete_transaction(self) -> None:
        transaction_id = create_transaction(self.client, "user-a", self.transaction)

        listed = list_transactions(self.client, "user-a")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], transaction_id)
        self.assertEqual(listed[0]["amount_cents"], 2599)
        self.assertEqual(listed[0]["time"], "12:30")

        updated: TransactionData = {
            **self.transaction,
            "description": "Compra do mês",
            "amount_cents": 3199,
        }
        update_transaction(self.client, "user-a", transaction_id, updated)
        listed = list_transactions(self.client, "user-a")
        self.assertEqual(listed[0]["description"], "Compra do mês")
        self.assertEqual(listed[0]["amount_cents"], 3199)

        delete_transaction(self.client, "user-a", transaction_id)
        self.assertEqual(list_transactions(self.client, "user-a"), [])

    def test_legacy_transaction_uses_creation_time_for_report_time(self) -> None:
        self.client.collection_instance.documents["legacy"] = {
            "type": "Despesa",
            "description": "Conta antiga",
            "category": "Outros",
            "amount_cents": 1000,
            "date": "2026-10-14",
            "created_at": "2026-10-14T17:45:00+00:00",
            "owner_uid": "user-a",
        }

        listed = list_transactions(self.client, "user-a")
        self.assertEqual(len(listed[0]["time"]), 5)
        self.assertRegex(listed[0]["time"], r"^\d{2}:\d{2}$")

    def test_rejects_invalid_time(self) -> None:
        invalid_transaction: TransactionData = {
            **self.transaction,
            "time": "25:70",
        }
        with self.assertRaisesRegex(ValueError, "formato HH:MM"):
            create_transaction(self.client, "user-a", invalid_transaction)

    def test_updates_transaction_category_and_type(self) -> None:
        transaction_id = create_transaction(self.client, "user-a", self.transaction)

        update_transaction_category(
            self.client,
            "user-a",
            transaction_id,
            "Receita",
            "Freelance",
        )

        updated = list_transactions(self.client, "user-a")[0]
        self.assertEqual(updated["type"], "Receita")
        self.assertEqual(updated["category"], "Freelance")

    def test_rejects_invalid_category_update(self) -> None:
        with self.assertRaisesRegex(ValueError, "tipo deve ser Receita ou Despesa"):
            update_transaction_category(
                self.client,
                "user-a",
                "missing",
                "Investimento",
                "Outros",
            )

    def test_rejects_invalid_amount_without_writing(self) -> None:
        invalid_transaction: TransactionData = {
            **self.transaction,
            "amount_cents": 0,
        }
        with self.assertRaisesRegex(ValueError, "maior que zero"):
            create_transaction(self.client, "user-a", invalid_transaction)

        self.assertEqual(list_transactions(self.client, "user-a"), [])

    def test_missing_transaction_cannot_be_updated_or_deleted(self) -> None:
        with self.assertRaisesRegex(ValueError, "não existe mais"):
            update_transaction(
                self.client,
                "user-a",
                "missing",
                self.transaction,
            )
        with self.assertRaisesRegex(ValueError, "não existe mais"):
            delete_transaction(self.client, "user-a", "missing")

    def test_users_cannot_read_or_modify_each_others_transactions(self) -> None:
        transaction_id = create_transaction(self.client, "user-a", self.transaction)

        self.assertEqual(list_transactions(self.client, "user-b"), [])
        with self.assertRaisesRegex(ValueError, "não existe mais"):
            update_transaction(
                self.client,
                "user-b",
                transaction_id,
                self.transaction,
            )
        with self.assertRaisesRegex(ValueError, "não existe mais"):
            delete_transaction(self.client, "user-b", transaction_id)

        self.assertEqual(len(list_transactions(self.client, "user-a")), 1)

    def test_first_google_user_claims_and_migrates_legacy_transactions(self) -> None:
        self.client.collection_instance.documents["legacy-1"] = {
            **self.transaction,
        }
        self.client.collection_instance.documents["legacy-2"] = {
            **self.transaction,
            "description": "Outra despesa",
        }

        ensure_legacy_data_owner(self.client, "user-a")
        ensure_legacy_data_owner(self.client, "user-b")

        metadata = self.client.collection(LEGACY_OWNER_COLLECTION).documents[
            LEGACY_OWNER_DOCUMENT
        ]
        self.assertEqual(metadata["uid"], "user-a")
        self.assertTrue(metadata["migration_complete"])
        self.assertEqual(len(list_transactions(self.client, "user-a")), 2)
        self.assertEqual(list_transactions(self.client, "user-b"), [])


if __name__ == "__main__":
    unittest.main()
