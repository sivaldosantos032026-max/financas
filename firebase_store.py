from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, TypedDict

import firebase_admin
import streamlit as st
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials, firestore
from google.api_core.exceptions import AlreadyExists, GoogleAPICallError
from google.cloud.firestore_v1 import Client, FieldFilter


COLLECTION_NAME = "lancamentos_financeiros"
LEGACY_OWNER_COLLECTION = "_app_metadata"
LEGACY_OWNER_DOCUMENT = "legacy_data_owner"


class TransactionData(TypedDict):
    type: str
    description: str
    category: str
    amount_cents: int
    date: str
    time: str


class Transaction(TransactionData):
    id: str


@st.cache_resource
def get_firestore_client() -> Client:
    firebase_app = get_firebase_app()
    try:
        return firestore.client(app=firebase_app)
    except (ValueError, GoogleAPICallError) as exc:
        raise RuntimeError(f"Não foi possível inicializar o Firestore: {exc}") from exc


@st.cache_resource
def get_firebase_app() -> firebase_admin.App:
    try:
        firebase_settings = st.secrets["firebase"]
        project_id = str(firebase_settings["project_id"]).strip()
        service_account = dict(firebase_settings["service_account"])
    except (FileNotFoundError, KeyError, TypeError) as exc:
        raise ValueError(
            "Configure [firebase] e [firebase.service_account] em "
            ".streamlit/secrets.toml. Consulte README.md."
        ) from exc

    if not project_id:
        raise ValueError("O campo firebase.project_id não pode ficar vazio.")
    if service_account.get("project_id") != project_id:
        raise ValueError(
            "firebase.project_id deve ser igual ao project_id da conta de serviço."
        )

    app_name = "financas-pessoais"
    try:
        firebase_app = firebase_admin.get_app(app_name)
    except ValueError:
        firebase_app = firebase_admin.initialize_app(
            credentials.Certificate(service_account),
            {"projectId": project_id},
            name=app_name,
        )

    return firebase_app


def verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    if not id_token:
        raise ValueError("Faça login com sua conta Google para continuar.")
    return firebase_auth.verify_id_token(
        id_token,
        app=get_firebase_app(),
    )


def ensure_legacy_data_owner(client: Client, user_uid: str) -> None:
    owner_ref = (
        client.collection(LEGACY_OWNER_COLLECTION).document(LEGACY_OWNER_DOCUMENT)
    )
    try:
        owner_ref.create(
            {
                "uid": user_uid,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except AlreadyExists:
        pass

    owner_snapshot = owner_ref.get()
    owner_data = owner_snapshot.to_dict()
    if owner_data is None or not isinstance(owner_data.get("uid"), str):
        raise RuntimeError("Não foi possível verificar o proprietário dos dados antigos.")
    if owner_data["uid"] != user_uid:
        return
    if owner_data.get("migration_complete") is True:
        return

    legacy_documents = [
        document
        for document in client.collection(COLLECTION_NAME).stream()
        if "owner_uid" not in (document.to_dict() or {})
    ]
    for start in range(0, len(legacy_documents), 450):
        batch = client.batch()
        for document in legacy_documents[start : start + 450]:
            batch.update(document.reference, {"owner_uid": user_uid})
        batch.commit()
    owner_ref.update({"migration_complete": True})


def _validate_transaction(data: Mapping[str, Any]) -> TransactionData:
    required = {"type", "description", "category", "amount_cents", "date", "time"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Campos obrigatórios ausentes: {', '.join(sorted(missing))}.")
    transaction_type = data["type"]
    description = data["description"]
    category = data["category"]
    amount_cents = data["amount_cents"]
    transaction_date = data["date"]
    transaction_time = data["time"]

    if not isinstance(transaction_type, str) or transaction_type not in {
        "Receita",
        "Despesa",
    }:
        raise ValueError("O tipo deve ser Receita ou Despesa.")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("A descrição não pode ficar vazia.")
    if not isinstance(category, str) or not category.strip():
        raise ValueError("A categoria não pode ficar vazia.")
    if (
        not isinstance(amount_cents, int)
        or isinstance(amount_cents, bool)
        or amount_cents <= 0
    ):
        raise ValueError("O valor deve ser maior que zero e informado em centavos.")
    if not isinstance(transaction_date, str):
        raise ValueError("A data deve estar no formato AAAA-MM-DD.")
    try:
        datetime.strptime(transaction_date, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("A data deve estar no formato AAAA-MM-DD.") from exc
    if not isinstance(transaction_time, str):
        raise ValueError("A hora deve estar no formato HH:MM.")
    try:
        datetime.strptime(transaction_time, "%H:%M")
    except ValueError as exc:
        raise ValueError("A hora deve estar no formato HH:MM.") from exc
    return TransactionData(
        type=transaction_type,
        description=description,
        category=category,
        amount_cents=amount_cents,
        date=transaction_date,
        time=transaction_time,
    )


def list_transactions(client: Client, user_uid: str) -> list[Transaction]:
    transactions: list[Transaction] = []
    owned_query = client.collection(COLLECTION_NAME).where(
        filter=FieldFilter("owner_uid", "==", user_uid)
    )
    for document in owned_query.stream():
        data = document.to_dict()
        if data is None:
            raise ValueError(f"O lançamento {document.id} não contém dados.")
        transaction_record = dict(data)
        if "time" not in transaction_record:
            created_at = transaction_record.get("created_at")
            if isinstance(created_at, str):
                try:
                    transaction_record["time"] = datetime.fromisoformat(
                        created_at
                    ).astimezone().strftime("%H:%M")
                except ValueError:
                    transaction_record["time"] = "00:00"
            else:
                transaction_record["time"] = "00:00"
        transaction_data = _validate_transaction(transaction_record)
        transactions.append(Transaction(id=document.id, **transaction_data))
    return sorted(transactions, key=lambda item: item["date"], reverse=True)


def create_transaction(client: Client, user_uid: str, data: TransactionData) -> str:
    validated_data = _validate_transaction(data)
    payload = {
        **validated_data,
        "owner_uid": user_uid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    document = client.collection(COLLECTION_NAME).document()
    document.set(payload)
    return document.id


def update_transaction(
    client: Client, user_uid: str, transaction_id: str, data: TransactionData
) -> None:
    validated_data = _validate_transaction(data)
    document = client.collection(COLLECTION_NAME).document(transaction_id)
    snapshot = document.get()
    snapshot_data = snapshot.to_dict()
    if (
        not snapshot.exists
        or snapshot_data is None
        or snapshot_data.get("owner_uid") != user_uid
    ):
        raise ValueError("O lançamento selecionado não existe mais.")
    document.update(validated_data)


def update_transaction_category(
    client: Client,
    user_uid: str,
    transaction_id: str,
    transaction_type: str,
    category: str,
) -> None:
    if transaction_type not in {"Receita", "Despesa"}:
        raise ValueError("O tipo deve ser Receita ou Despesa.")
    if not category.strip():
        raise ValueError("A categoria não pode ficar vazia.")

    document = client.collection(COLLECTION_NAME).document(transaction_id)
    snapshot = document.get()
    snapshot_data = snapshot.to_dict()
    if (
        not snapshot.exists
        or snapshot_data is None
        or snapshot_data.get("owner_uid") != user_uid
    ):
        raise ValueError("O lançamento selecionado não existe mais.")
    document.update({"type": transaction_type, "category": category})


def delete_transaction(client: Client, user_uid: str, transaction_id: str) -> None:
    document = client.collection(COLLECTION_NAME).document(transaction_id)
    snapshot = document.get()
    snapshot_data = snapshot.to_dict()
    if (
        not snapshot.exists
        or snapshot_data is None
        or snapshot_data.get("owner_uid") != user_uid
    ):
        raise ValueError("O lançamento selecionado não existe mais.")
    document.delete()
