from datetime import date, datetime, time
from decimal import Decimal

import altair as alt
import streamlit as st
from firebase_admin import auth as firebase_admin_auth
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError

from firebase_auth import get_firebase_web_config, google_sign_in
from firebase_store import (
    Transaction,
    TransactionData,
    create_transaction,
    delete_transaction,
    ensure_legacy_data_owner,
    get_firestore_client,
    list_transactions,
    update_transaction,
    update_transaction_category,
    verify_firebase_id_token,
)


CATEGORIES = {
    "Receita": [
        "Salário",
        "Freelance",
        "Investimentos",
        "Aluguel recebido",
        "Vendas",
        "Comissões",
        "Bônus",
        "Aposentadoria",
        "Benefícios",
        "Reembolso",
        "Presente recebido",
        "Outros",
    ],
    "Despesa": [
        "Moradia",
        "Alimentação",
        "Transporte",
        "Saúde",
        "Educação",
        "Lazer",
        "Contas de consumo",
        "Internet e telefone",
        "Compras e vestuário",
        "Assinaturas",
        "Impostos e taxas",
        "Seguros",
        "Pets",
        "Viagens",
        "Empréstimos e dívidas",
        "Doações",
        "Cuidados pessoais",
        "Serviços",
        "Outros",
    ],
}
MONTHLY_SPENDING_ALERT_CENTS = 150_000


def format_brl(amount_cents: int) -> str:
    amount = f"{amount_cents / 100:,.2f}"
    amount = amount.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {amount}"


def display_records(records: list[Transaction]) -> list[dict[str, str]]:
    return [
        {
            "Data": date.fromisoformat(record["date"]).strftime("%d/%m/%Y"),
            "Hora": record["time"],
            "Tipo": record["type"],
            "Descrição": record["description"],
            "Categoria": record["category"],
            "Valor": format_brl(record["amount_cents"]),
        }
        for record in records
    ]


def build_expense_heatmap_data(
    records: list[Transaction], start_date: date, end_date: date
) -> tuple[list[dict[str, str | float]], list[str], list[str]]:
    months: list[str] = []
    month_cursor = date(start_date.year, start_date.month, 1)
    final_month = date(end_date.year, end_date.month, 1)
    while month_cursor <= final_month:
        months.append(month_cursor.strftime("%Y-%m"))
        if month_cursor.month == 12:
            month_cursor = date(month_cursor.year + 1, 1, 1)
        else:
            month_cursor = date(month_cursor.year, month_cursor.month + 1, 1)

    categories = sorted(
        set(CATEGORIES["Despesa"])
        | {
            record["category"]
            for record in records
            if record["type"] == "Despesa"
        }
    )
    totals: dict[tuple[str, str], int] = {}
    for record in records:
        record_date = date.fromisoformat(record["date"])
        if (
            record["type"] == "Despesa"
            and start_date <= record_date <= end_date
        ):
            month = record_date.strftime("%Y-%m")
            key = (month, record["category"])
            totals[key] = totals.get(key, 0) + record["amount_cents"]

    heatmap_data = [
        {
            "month": month,
            "category": category,
            "amount_reais": totals.get((month, category), 0) / 100,
        }
        for month in months
        for category in categories
    ]
    return heatmap_data, months, categories


st.set_page_config(
    page_title="Finanças pessoais",
    page_icon=":material/account_balance_wallet:",
    layout="wide",
)
st.title("Finanças pessoais")
st.caption("Acompanhe suas receitas, despesas e saldo em um só lugar.")

try:
    firebase_web_config = get_firebase_web_config()
except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
    st.error(str(exc), title="Configuração do login Google")
    st.stop()

with st.sidebar:
    auth_state = google_sign_in(firebase_web_config, key="firebase_google_auth")

auth_error_messages = {
    "auth/unauthorized-domain": (
        "Este domínio não está autorizado no Firebase Authentication. "
        "Adicione-o em Authentication > Settings > Authorized domains."
    ),
    "auth/popup-closed-by-user": "O login foi cancelado antes de ser concluído.",
    "auth/popup-blocked": "O navegador bloqueou a janela de login. Permita pop-ups e tente novamente.",
    "auth/account-exists-with-different-credential": (
        "Esta conta Google já está vinculada a outro método de login no Firebase."
    ),
    "firebase/web-config-missing": (
        "Configure api_key, auth_domain, project_id e app_id "
        "na seção [firebase] de .streamlit/secrets.toml."
    ),
    "auth/sdk-load-failed": (
        "Não foi possível carregar o SDK de autenticação Firebase. "
        "Verifique a conexão com a internet e tente novamente."
    ),
}
if auth_state["error"]:
    st.error(
        auth_error_messages.get(
            auth_state["error"],
            f"Falha no login Google ({auth_state['error']}).",
        ),
        title="Autenticação Firebase",
    )

id_token = auth_state["idToken"]
if not id_token:
    st.info(
        "Entre com sua conta Google para acessar seus lançamentos. "
        "Uma sessão Firebase já ativa será restaurada automaticamente."
    )
    st.stop()

try:
    firebase_claims = verify_firebase_id_token(id_token)
except (
    firebase_admin_auth.InvalidIdTokenError,
    firebase_admin_auth.ExpiredIdTokenError,
    firebase_admin_auth.RevokedIdTokenError,
    firebase_admin_auth.CertificateFetchError,
    FirebaseError,
    GoogleAPICallError,
    ValueError,
    RuntimeError,
) as exc:
    st.error(
        f"Não foi possível validar sua sessão do Firebase: {exc}",
        title="Sessão inválida",
    )
    st.stop()

user_uid = firebase_claims.get("uid")
if not isinstance(user_uid, str) or not user_uid:
    st.error("A autenticação não retornou um identificador Firebase válido.")
    st.stop()

if "flash_message" in st.session_state:
    st.success(st.session_state.pop("flash_message"))

try:
    firestore_client = get_firestore_client()
except (FileNotFoundError, KeyError, TypeError, ValueError, RuntimeError) as exc:
    st.error(str(exc), title="Configuração do Firebase")
    st.stop()

try:
    ensure_legacy_data_owner(firestore_client, user_uid)
    transactions = list_transactions(firestore_client, user_uid)
except ValueError as exc:
    st.error(str(exc), title="Dados inválidos no Firestore")
    st.stop()
except (GoogleAPICallError, FirebaseError, RuntimeError) as exc:
    st.error(
        f"Não foi possível acessar o Firestore: {exc}",
        title="Falha na conexão",
    )
    st.stop()

section = st.sidebar.segmented_control(
    "Seção",
    ["Visão geral", "Novo lançamento", "Gerenciar lançamentos", "Relatório"],
    default="Visão geral",
)

today = date.today()
try:
    default_start_date = today.replace(year=today.year - 1)
except ValueError:
    default_start_date = today.replace(year=today.year - 1, day=28)

with st.sidebar:
    selected_period = st.date_input(
        "Período (padrão: últimos 12 meses)",
        value=(default_start_date, today),
        min_value=date(1900, 1, 1),
        max_value=today,
        format="DD/MM/YYYY",
    )

if len(selected_period) != 2:
    st.info("Selecione as datas inicial e final para aplicar o filtro.")
    st.stop()

period_start, period_end = selected_period
if period_start > period_end:
    st.error("A data inicial deve ser anterior ou igual à data final.")
    st.stop()

filtered_transactions = [
    item
    for item in transactions
    if period_start <= date.fromisoformat(item["date"]) <= period_end
]
income_cents = sum(
    item["amount_cents"]
    for item in filtered_transactions
    if item["type"] == "Receita"
)
expense_cents = sum(
    item["amount_cents"]
    for item in filtered_transactions
    if item["type"] == "Despesa"
)
balance_cents = income_cents - expense_cents
month_start = today.replace(day=1)
current_month_expenses = sum(
    item["amount_cents"]
    for item in transactions
    if item["type"] == "Despesa"
    and month_start <= date.fromisoformat(item["date"]) <= today
)

if section == "Visão geral":
    st.subheader("Resumo geral")
    st.caption(
        f"Valores do período: {period_start.strftime('%d/%m/%Y')} a "
        f"{period_end.strftime('%d/%m/%Y')}."
    )
    with st.container(horizontal=True):
        st.metric("Saldo", format_brl(balance_cents), border=True)
        st.metric("Receitas", format_brl(income_cents), border=True)
        st.metric("Despesas", format_brl(expense_cents), border=True)

    st.subheader("Alerta de gastos do mês")
    st.progress(
        min(current_month_expenses / MONTHLY_SPENDING_ALERT_CENTS, 1.0),
        text=(
            f"{format_brl(current_month_expenses)} de "
            f"{format_brl(MONTHLY_SPENDING_ALERT_CENTS)}"
        ),
    )
    if current_month_expenses >= MONTHLY_SPENDING_ALERT_CENTS:
        st.warning(
            f"As despesas deste mês atingiram o limite de "
            f"{format_brl(MONTHLY_SPENDING_ALERT_CENTS)}.",
            title="Limite mensal de gastos atingido",
        )
    else:
        st.info(
            f"Restam {format_brl(MONTHLY_SPENDING_ALERT_CENTS - current_month_expenses)} "
            "até o limite mensal."
        )

    st.subheader("Mapa de calor dos gastos")
    if filtered_transactions:
        heatmap_data, heatmap_months, heatmap_categories = (
            build_expense_heatmap_data(
                filtered_transactions, period_start, period_end
            )
        )
        heatmap = (
            alt.Chart(alt.Data(values=heatmap_data))
            .mark_rect()
            .encode(
                x=alt.X(
                    "month:N",
                    title="Mês",
                    sort=heatmap_months,
                    axis=alt.Axis(labelAngle=-45),
                ),
                y=alt.Y("category:N", title="Categoria", sort=heatmap_categories),
                color=alt.Color(
                    "amount_reais:Q",
                    title="Gastos (R$)",
                    scale=alt.Scale(scheme="oranges", domainMin=0),
                ),
                tooltip=[
                    alt.Tooltip("month:N", title="Mês"),
                    alt.Tooltip("category:N", title="Categoria"),
                    alt.Tooltip(
                        "amount_reais:Q", title="Gastos (R$)", format=",.2f"
                    ),
                ],
            )
        )
        st.altair_chart(
            heatmap,
            alt="Despesas mensais por categoria no período selecionado",
            width="stretch",
        )
    else:
        st.info("Não há lançamentos no período para montar o mapa de calor.")

    st.subheader("Lançamentos recentes")
    recent_transactions = sorted(
        filtered_transactions,
        key=lambda item: (item["date"], item["time"]),
        reverse=True,
    )[:10]
    if recent_transactions:
        st.dataframe(
            display_records(recent_transactions),
            hide_index=True,
            alt="Lançamentos financeiros recentes",
        )
    else:
        st.info("Nenhum lançamento encontrado nesse período.")

elif section == "Novo lançamento":
    st.subheader("Adicionar lançamento")
    transaction_type = st.selectbox("Tipo", list(CATEGORIES), key="new_type")
    with st.form("new_transaction", border=True):
        description = st.text_input("Descrição", max_chars=120)
        category = st.selectbox("Categoria", CATEGORIES[transaction_type])
        amount = st.number_input(
            "Valor (R$)",
            min_value=0.01,
            max_value=1_000_000_000.00,
            value=0.01,
            step=0.01,
            format="%.2f",
        )
        transaction_date = st.date_input("Data", value=date.today())
        transaction_time = st.time_input("Hora", value=datetime.now().time().replace(second=0, microsecond=0))
        submitted = st.form_submit_button(
            "Salvar lançamento",
            type="primary",
            icon=":material/save:",
            width="stretch",
        )

    if submitted:
        if not description.strip():
            st.error("Informe uma descrição para o lançamento.")
        else:
            payload: TransactionData = {
                "type": transaction_type,
                "description": description.strip(),
                "category": category,
                "amount_cents": int(Decimal(str(amount)) * 100),
                "date": transaction_date.isoformat(),
                "time": transaction_time.strftime("%H:%M"),
            }
            try:
                create_transaction(firestore_client, user_uid, payload)
            except (GoogleAPICallError, ValueError) as exc:
                st.error(f"Não foi possível salvar o lançamento: {exc}")
            else:
                st.session_state["flash_message"] = "Lançamento salvo com sucesso."
                st.rerun()

elif section == "Gerenciar lançamentos":
    st.subheader("Gerenciar lançamentos")
    if not transactions:
        st.info("Ainda não há lançamentos para gerenciar.")
    else:
        st.dataframe(
            display_records(
                sorted(
                    transactions,
                    key=lambda item: (item["date"], item["time"]),
                    reverse=True,
                )
            ),
            hide_index=True,
            alt="Todos os lançamentos financeiros",
        )

        record_by_id = {record["id"]: record for record in transactions}
        selected_id = st.selectbox(
            "Selecione um lançamento para editar ou excluir",
            options=list(record_by_id),
            format_func=lambda record_id: (
                f"{date.fromisoformat(record_by_id[record_id]['date']).strftime('%d/%m/%Y')} · "
                f"{record_by_id[record_id]['time']} · "
                f"{record_by_id[record_id]['description']} · "
                f"{format_brl(record_by_id[record_id]['amount_cents'])}"
            ),
        )
        selected_record = record_by_id[selected_id]
        st.markdown("**Alterar categoria do lançamento**")
        category_type = st.selectbox(
            "Tipo",
            list(CATEGORIES),
            index=list(CATEGORIES).index(selected_record["type"]),
            key=f"category_type_{selected_id}",
        )
        category_options = list(CATEGORIES[category_type])
        if (
            category_type == selected_record["type"]
            and selected_record["category"] not in category_options
        ):
            category_options.append(selected_record["category"])
        category_index = (
            category_options.index(selected_record["category"])
            if selected_record["category"] in category_options
            else 0
        )
        changed_category = st.selectbox(
            "Categoria",
            category_options,
            index=category_index,
            key=f"category_value_{selected_id}",
        )
        if st.button(
            "Alterar categoria",
            icon=":material/category:",
            type="primary",
            key=f"change_category_{selected_id}",
        ):
            try:
                update_transaction_category(
                    firestore_client,
                    user_uid,
                    selected_id,
                    category_type,
                    changed_category,
                )
            except (GoogleAPICallError, ValueError) as exc:
                st.error(f"Não foi possível alterar a categoria: {exc}")
            else:
                st.session_state["flash_message"] = "Categoria alterada com sucesso."
                st.rerun()

        with st.form(f"edit_transaction_{selected_id}", border=True):
            st.markdown("**Editar lançamento**")
            edited_description = st.text_input(
                "Descrição",
                value=selected_record["description"],
                max_chars=120,
                key=f"edit_description_{selected_id}",
            )
            edited_amount = st.number_input(
                "Valor (R$)",
                min_value=0.01,
                max_value=1_000_000_000.00,
                value=selected_record["amount_cents"] / 100,
                step=0.01,
                format="%.2f",
                key=f"edit_amount_{selected_id}",
            )
            edited_date = st.date_input(
                "Data",
                value=date.fromisoformat(selected_record["date"]),
                key=f"edit_date_{selected_id}",
            )
            edited_time = st.time_input(
                "Hora",
                value=time.fromisoformat(selected_record["time"]),
                key=f"edit_time_{selected_id}",
            )
            update_submitted = st.form_submit_button(
                "Salvar alterações",
                type="primary",
                icon=":material/save:",
                width="stretch",
            )

        if update_submitted:
            if not edited_description.strip():
                st.error("Informe uma descrição para o lançamento.")
            else:
                updated_payload: TransactionData = {
                    "type": selected_record["type"],
                    "description": edited_description.strip(),
                    "category": selected_record["category"],
                    "amount_cents": int(Decimal(str(edited_amount)) * 100),
                    "date": edited_date.isoformat(),
                    "time": edited_time.strftime("%H:%M"),
                }
                try:
                    update_transaction(
                        firestore_client,
                        user_uid,
                        selected_id,
                        updated_payload,
                    )
                except (GoogleAPICallError, ValueError) as exc:
                    st.error(f"Não foi possível atualizar o lançamento: {exc}")
                else:
                    st.session_state["flash_message"] = "Lançamento atualizado com sucesso."
                    st.rerun()

        st.divider()
        confirm_delete = st.checkbox(
            "Confirmo que desejo excluir este lançamento.",
            key=f"confirm_delete_{selected_id}",
        )
        if st.button(
            "Excluir lançamento",
            type="secondary",
            icon=":material/delete:",
            disabled=not confirm_delete,
        ):
            try:
                delete_transaction(firestore_client, user_uid, selected_id)
            except (GoogleAPICallError, ValueError) as exc:
                st.error(f"Não foi possível excluir o lançamento: {exc}")
            else:
                st.session_state["flash_message"] = "Lançamento excluído com sucesso."
                st.rerun()

elif section == "Relatório":
    st.subheader("Relatório de lançamentos")
    st.caption(
        f"Período: {period_start.strftime('%d/%m/%Y')} a "
        f"{period_end.strftime('%d/%m/%Y')}."
    )
    if filtered_transactions:
        report_transactions = sorted(
            filtered_transactions,
            key=lambda item: (item["date"], item["time"]),
            reverse=True,
        )
        st.metric("Total no período", format_brl(income_cents + expense_cents))
        st.dataframe(
            display_records(report_transactions),
            hide_index=True,
            alt="Relatório com valor, categoria, data e hora dos lançamentos",
        )
    else:
        st.info("Nenhum lançamento encontrado no período selecionado.")
