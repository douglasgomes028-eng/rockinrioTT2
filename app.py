"""Dashboard de vendas — Grupo Impettus Rock In Rio 26."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from zig_client import (
    EVENTO_INICIO_DEFAULT,
    TZ,
    DiaOperacional,
    ZigClient,
    janela_operacional,
)

st.set_page_config(
    page_title="Impettus | RIR26 Vendas",
    page_icon="R",
    layout="wide",
    initial_sidebar_state="collapsed",
)

REFRESH_SECONDS = 60
HISTORICO_TTL_SECONDS = 30 * 60


def _money(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _int_br(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _load_secrets() -> dict:
    zig = st.secrets.get("zig", {})
    return {
        "login": zig.get("login") or st.secrets.get("ZIG_LOGIN", ""),
        "password": zig.get("password") or st.secrets.get("ZIG_PASSWORD", ""),
        "evento_id": int(zig.get("evento_id") or st.secrets.get("ZIG_EVENTO_ID", 38049)),
    }


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def carregar_snapshot(login: str, password: str, evento_id: int, _tick: int):
    """Atual (sem histórico pesado) a cada ~1 min."""
    client = ZigClient(login=login, password=password, evento_id=evento_id)
    return client.fetch_snapshot(
        inicio_evento=EVENTO_INICIO_DEFAULT,
        incluir_historico=False,
    )


@st.cache_data(ttl=HISTORICO_TTL_SECONDS, show_spinner=False)
def carregar_historico(login: str, password: str, evento_id: int, _bucket: int):
    """Dias anteriores (janelas já fechadas). Cache mais longo."""
    client = ZigClient(login=login, password=password, evento_id=evento_id)
    return client.fetch_historico(inicio_evento=EVENTO_INICIO_DEFAULT)


def _render_pontos(pontos, chart_key: str) -> None:
    if not pontos:
        st.caption("Sem vendas por ponto neste período.")
        return

    df = pd.DataFrame(
        [
            {
                "Ponto": p.nome,
                "Faturamento": p.total,
                "Quantidade": p.quantidade,
            }
            for p in pontos
        ]
    )
    df.insert(0, "Posição", range(1, len(df) + 1))
    df["Faturamento (R$)"] = df["Faturamento"].map(_money)

    fig = px.bar(
        df,
        x="Faturamento",
        y="Ponto",
        orientation="h",
        text="Faturamento (R$)",
        category_orders={"Ponto": list(df["Ponto"])},
        labels={"Faturamento": "Faturamento (R$)", "Ponto": "Ponto de venda"},
    )
    fig.update_layout(
        height=max(280, 44 * len(df)),
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis=dict(autorange="reversed"),
        showlegend=False,
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    st.plotly_chart(fig, use_container_width=True, key=chart_key)
    st.dataframe(
        df[["Posição", "Ponto", "Quantidade", "Faturamento (R$)"]],
        use_container_width=True,
        hide_index=True,
    )


def _render_dia_metrics(dia: DiaOperacional) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Faturamento", _money(dia.faturamento))
    c2.metric("Transações", _int_br(dia.transacoes))
    c3.metric("Ticket médio", _money(dia.ticket_medio))
    c4.metric("Itens vendidos", _int_br(dia.itens))


def _render_historico(historico: list[DiaOperacional]) -> None:
    st.divider()
    st.subheader("Dias anteriores")
    st.caption(
        "Mesmas métricas do dia operacional, na faixa **12:00 - 07:00** (Brasília). "
        "Do mais recente ao mais antigo."
    )

    if not historico:
        st.info("Ainda não há dias operacionais anteriores encerrados.")
        return

    # Resumo rápido em tabela
    resumo = pd.DataFrame(
        [
            {
                "Dia operacional": d.label,
                "Faturamento": d.faturamento,
                "Transações": d.transacoes,
                "Ticket médio": d.ticket_medio,
                "Itens": d.itens,
            }
            for d in historico
            if not d.erro
        ]
    )
    if not resumo.empty:
        view = resumo.copy()
        view["Faturamento"] = view["Faturamento"].map(_money)
        view["Transações"] = view["Transações"].map(_int_br)
        view["Ticket médio"] = view["Ticket médio"].map(_money)
        view["Itens"] = view["Itens"].map(_int_br)
        st.dataframe(view, use_container_width=True, hide_index=True)

    for i, dia in enumerate(historico):
        titulo = f"{dia.label}  ·  {_money(dia.faturamento)}"
        with st.expander(titulo, expanded=False):
            if dia.erro:
                st.error(f"Falha ao carregar este dia: {dia.erro}")
                continue
            st.caption(f"Período consultado: {dia.periodo}")
            _render_dia_metrics(dia)
            st.markdown("**Vendas por ponto**")
            _render_pontos(dia.pontos, chart_key=f"hist_ponto_{i}_{dia.inicio.date()}")


def main() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.2rem; padding-bottom: 1rem; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Grupo Impettus — Rock In Rio 26")
    st.caption("Dashboard de vendas em tempo quase real (atualização a cada 1 minuto)")

    try:
        cfg = _load_secrets()
    except Exception:
        st.error(
            "Configure os secrets no Streamlit Cloud (ou em `.streamlit/secrets.toml`). "
            "Veja `secrets.toml.example`."
        )
        st.stop()

    if not cfg["login"] or not cfg["password"]:
        st.error("Secrets incompletos: informe `zig.login` e `zig.password`.")
        st.stop()

    agora = datetime.now(TZ)
    tick = int(agora.timestamp() // REFRESH_SECONDS)
    hist_bucket = int(agora.timestamp() // HISTORICO_TTL_SECONDS)
    dia_ini, dia_fim = janela_operacional(agora)

    with st.spinner("Buscando vendas no Zig/netPDV..."):
        snap = carregar_snapshot(cfg["login"], cfg["password"], cfg["evento_id"], tick)

    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.caption(
            f"Período total: **{snap.periodo_total or '—'}** · "
            f"Dia operacional (12:00–07:00): **{snap.periodo_dia or '—'}**"
        )
    with col_b:
        st.caption(
            f"Última atualização: **{snap.gerado_em.strftime('%d/%m/%Y %H:%M:%S')}** · "
            f"próximo refresh ~{REFRESH_SECONDS}s"
        )

    if snap.erro:
        st.error(f"Erro ao consultar o backoffice: {snap.erro}")
        st.info("O app tenta novamente no próximo ciclo de 60 segundos.")
        st.stop()

    st.subheader("Faturamento total")
    st.metric("Do primeiro dia até agora", _money(snap.faturamento_total))

    st.subheader("Dia operacional")
    st.caption(
        f"Janela atual: {dia_ini.strftime('%d/%m/%Y %H:%M')} - "
        f"{dia_fim.strftime('%d/%m/%Y %H:%M')} (Brasília)"
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Faturamento do dia", _money(snap.faturamento_dia))
    c2.metric("Transações do dia", _int_br(snap.transacoes_dia))
    c3.metric("Ticket médio do dia", _money(snap.ticket_medio_dia))
    c4.metric("Itens vendidos do dia", _int_br(snap.itens_dia))

    st.subheader("Vendas por ponto (dia operacional)")
    if not snap.pontos:
        st.warning("Nenhum ponto com vendas no período operacional atual.")
    else:
        _render_pontos(snap.pontos, chart_key="pontos_atual")

    with st.spinner("Carregando dias anteriores..."):
        try:
            historico = carregar_historico(
                cfg["login"], cfg["password"], cfg["evento_id"], hist_bucket
            )
        except Exception as exc:  # noqa: BLE001
            historico = []
            st.warning(f"Não foi possível carregar o histórico agora: {exc}")

    _render_historico(historico)

    try:
        from streamlit_autorefresh import st_autorefresh as _ar

        _ar(interval=REFRESH_SECONDS * 1000, key="impettus_refresh")
    except Exception:
        st.button("Atualizar agora", type="primary")
        st.caption("Auto-refresh indisponível; use o botão ou recarregue a página.")


if __name__ == "__main__":
    main()
