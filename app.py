"""Dashboard de vendas — Grupo Impettus Rock In Rio 26."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from zig_client import (
    COR_AZUL,
    COR_LARANJA,
    COR_ROSA,
    COR_VERDE,
    CORES_CATEGORIA,
    CORES_PALCO,
    DIAS_OFICIAIS,
    EVENTO_INICIO_DEFAULT,
    TZ,
    DiaOperacional,
    ItemValor,
    SaidaHorariaPonto,
    ZigClient,
    janela_operacional,
    produtos_por_marca,
)

st.set_page_config(
    page_title="Impettus | RIR26 Vendas",
    page_icon="R",
    layout="wide",
    initial_sidebar_state="collapsed",
)

REFRESH_SECONDS = 60
HISTORICO_TTL_SECONDS = 30 * 60
SAIDA_HORARIA_TTL_SECONDS = 15 * 60

CORES_PAGAMENTO = [COR_AZUL, COR_VERDE, COR_LARANJA, COR_ROSA, "#775DD0", "#546E7A"]


def _money(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _int_br(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _pct(v: float, total: float) -> str:
    if total <= 0:
        return "0%"
    return f"{(v / total) * 100:.0f}%"


def _load_secrets() -> dict:
    zig = st.secrets.get("zig", {})
    return {
        "login": zig.get("login") or st.secrets.get("ZIG_LOGIN", ""),
        "password": zig.get("password") or st.secrets.get("ZIG_PASSWORD", ""),
        "evento_id": int(zig.get("evento_id") or st.secrets.get("ZIG_EVENTO_ID", 38049)),
    }


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def carregar_snapshot(login: str, password: str, evento_id: int, _tick: int):
    client = ZigClient(login=login, password=password, evento_id=evento_id)
    return client.fetch_snapshot(
        inicio_evento=EVENTO_INICIO_DEFAULT,
        incluir_historico=False,
    )


@st.cache_data(ttl=HISTORICO_TTL_SECONDS, show_spinner=False)
def carregar_historico(login: str, password: str, evento_id: int, _bucket: int):
    client = ZigClient(login=login, password=password, evento_id=evento_id)
    return client.fetch_historico(inicio_evento=EVENTO_INICIO_DEFAULT)


@st.cache_data(ttl=SAIDA_HORARIA_TTL_SECONDS, show_spinner=False)
def carregar_saida_horaria(login: str, password: str, evento_id: int, _bucket: int):
    """Somente janela operacional atual (12:00–07:00), cache mais longo."""
    client = ZigClient(login=login, password=password, evento_id=evento_id)
    return client.fetch_saida_horaria()


def _bar_ranking(df: pd.DataFrame, y_col: str, chart_key: str, height_row: int = 44) -> None:
    df = df.copy()
    df.insert(0, "Posição", range(1, len(df) + 1))
    df["Faturamento (R$)"] = df["Faturamento"].map(_money)
    fig = px.bar(
        df,
        x="Faturamento",
        y=y_col,
        orientation="h",
        text="Faturamento (R$)",
        category_orders={y_col: list(df[y_col])},
        labels={"Faturamento": "Faturamento (R$)", y_col: y_col},
    )
    fig.update_layout(
        height=max(280, height_row * len(df)),
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis=dict(autorange="reversed"),
        showlegend=False,
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    st.plotly_chart(fig, use_container_width=True, key=chart_key)
    cols = ["Posição", y_col, "Quantidade", "Faturamento (R$)"]
    st.dataframe(df[cols], use_container_width=True, hide_index=True)


def _render_pontos(pontos, chart_key: str) -> None:
    if not pontos:
        st.caption("Sem vendas por ponto neste período.")
        return
    df = pd.DataFrame(
        [{"Ponto": p.nome, "Faturamento": p.total, "Quantidade": p.quantidade} for p in pontos]
    )
    _bar_ranking(df, "Ponto", chart_key)


def _render_produtos_por_marca(produtos: list[ItemValor], key_prefix: str) -> None:
    st.subheader("Vendas por produto (por marca)")
    st.caption("Ranking de produtos no dia operacional, separados por marca.")
    if not produtos:
        st.warning("Nenhum produto encontrado no período.")
        return

    grupos = produtos_por_marca(produtos)
    if not grupos:
        st.warning("Nenhum produto classificado por marca.")
        return

    marcas = list(grupos.keys())
    # até 3 colunas por linha
    for i in range(0, len(marcas), 3):
        row = marcas[i : i + 3]
        cols = st.columns(len(row))
        for col, marca in zip(cols, row):
            with col:
                itens = grupos[marca][:12]
                total = sum(p.total for p in grupos[marca])
                st.markdown(f"**{marca}**")
                st.caption(f"Total: {_money(total)} · {len(grupos[marca])} produtos")
                df = pd.DataFrame(
                    [
                        {
                            "Produto": p.nome,
                            "Faturamento": p.total,
                            "Quantidade": p.quantidade,
                        }
                        for p in itens
                    ]
                )
                fig = px.bar(
                    df,
                    x="Faturamento",
                    y="Produto",
                    orientation="h",
                    text=df["Faturamento"].map(_money),
                    category_orders={"Produto": list(df["Produto"])},
                )
                fig.update_layout(
                    height=max(260, 36 * len(df)),
                    margin=dict(l=8, r=8, t=8, b=8),
                    yaxis=dict(autorange="reversed"),
                    showlegend=False,
                )
                fig.update_traces(textposition="outside", cliponaxis=False, marker_color=COR_AZUL)
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    key=f"{key_prefix}_marca_{marca}",
                )


def _donut(
    items: list[ItemValor],
    title: str,
    subtitle: str,
    colors: list[str],
    chart_key: str,
    semicircle: bool = False,
) -> None:
    st.markdown(f"**{title}**")
    st.caption(subtitle)
    if not items:
        st.caption("Sem dados.")
        return

    labels = [i.nome for i in items]
    values = [i.total for i in items]
    total = sum(values)
    if total <= 0:
        st.caption("Sem faturamento neste recorte.")
        return

    if semicircle:
        # meia-rosca: valores + fatia invisível
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=labels + [""],
                    values=values + [total],
                    hole=0.62,
                    sort=False,
                    direction="clockwise",
                    rotation=90,
                    textinfo="percent",
                    textposition="inside",
                    insidetextorientation="horizontal",
                    marker=dict(
                        colors=colors[: len(labels)] + ["rgba(0,0,0,0)"],
                        line=dict(color="#ffffff", width=2),
                    ),
                    hovertemplate="%{label}: %{value:,.2f}<extra></extra>",
                )
            ]
        )
        fig.update_layout(
            showlegend=True,
            legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=10, b=10),
            height=300,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        # esconde o label vazio da legenda
        fig.update_traces(textfont_size=14, textfont_color="white")
    else:
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.55,
                    sort=False,
                    textinfo="percent",
                    textposition="inside",
                    marker=dict(
                        colors=colors[: len(labels)],
                        line=dict(color="#ffffff", width=2),
                    ),
                    hovertemplate="%{label}: %{value:,.2f} (%{percent})<extra></extra>",
                )
            ]
        )
        fig.update_layout(
            showlegend=True,
            legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=10, b=10),
            height=300,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        fig.update_traces(textfont_size=14, textfont_color="white")

    st.plotly_chart(fig, use_container_width=True, key=chart_key)
    # mini tabela
    rows = [
        {
            "Item": i.nome,
            "Faturamento": _money(i.total),
            "%": _pct(i.total, total),
        }
        for i in items
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_mix_charts(
    palcos: list[ItemValor],
    formas: list[ItemValor],
    categorias: list[ItemValor],
    key_prefix: str,
) -> None:
    st.subheader("Mix do dia")
    c1, c2, c3 = st.columns(3)
    with c1:
        cores = [CORES_PALCO.get(p.nome, COR_AZUL) for p in palcos]
        _donut(
            palcos,
            "Venda por palco (%)",
            "participação de cada palco no faturamento",
            cores,
            f"{key_prefix}_palco",
            semicircle=True,
        )
    with c2:
        _donut(
            formas,
            "Forma de pagamento",
            "faturamento por forma",
            CORES_PAGAMENTO,
            f"{key_prefix}_pagto",
        )
    with c3:
        cores = [CORES_CATEGORIA.get(c.nome, COR_LARANJA) for c in categorias]
        _donut(
            categorias,
            "Categoria",
            "comida x bebida",
            cores,
            f"{key_prefix}_cat",
        )


def _saida_to_dataframe(slot: SaidaHorariaPonto) -> pd.DataFrame:
    if not slot.matriz or not slot.horas:
        return pd.DataFrame()
    rows = []
    for produto, por_hora in slot.matriz.items():
        row = {"Produto": produto}
        total = 0.0
        for h in slot.horas:
            q = float(por_hora.get(h, 0.0))
            row[h] = round(q, 1)
            total += q
        row["Total"] = round(total, 1)
        rows.append(row)
    return pd.DataFrame(rows)


def _render_saida_horaria(
    periodo_label: str,
    saidas: list[SaidaHorariaPonto],
    *,
    nested: bool = False,
    key_prefix: str = "saida",
) -> None:
    if nested:
        st.markdown("**Saída horária por produto (ponto / marca / palco)**")
    else:
        st.subheader("Saída horária por produto (ponto / marca / palco)")
    st.caption(
        f"Janela operacional **{periodo_label}** (faixas de 1h). "
        "Produtos da marca do ponto; bebidas rateadas pela participação do ponto no faturamento da hora."
    )

    if not saidas:
        st.info("Sem dados horários para esta janela.")
        return

    for palco in ("Mundo", "Sunset"):
        blocos = [s for s in saidas if s.palco == palco]
        if not blocos:
            continue
        st.markdown(f"{'####' if nested else '###'} Palco {palco}")
        for slot in blocos:
            titulo = f"{slot.ponto} · marca {slot.marca}"
            df = _saida_to_dataframe(slot)
            if nested:
                st.markdown(f"**{titulo}**")
                if df.empty:
                    st.caption("Sem saída de produtos neste ponto na janela.")
                else:
                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True,
                        key=f"{key_prefix}_{slot.ponto}",
                    )
            else:
                with st.expander(titulo, expanded=False):
                    if df.empty:
                        st.caption("Sem saída de produtos neste ponto na janela.")
                    else:
                        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_dia_metrics(dia: DiaOperacional) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Faturamento", _money(dia.faturamento))
    c2.metric("Transações", _int_br(dia.transacoes))
    c3.metric("Ticket médio", _money(dia.ticket_medio))
    c4.metric("Itens vendidos", _int_br(dia.itens))


def _render_historico(historico: list[DiaOperacional]) -> None:
    st.divider()
    st.subheader("Dias anteriores (cronograma oficial)")
    st.caption(
        "Somente dias oficiais de evento, faixa **12:00 - 07:00** (Brasília). "
        "Do mais recente ao mais antigo."
    )
    st.caption(
        "Dias oficiais: "
        + ", ".join(d.strftime("%d/%m/%Y") for d in DIAS_OFICIAIS)
    )

    if not historico:
        st.info("Ainda não há dias oficiais anteriores encerrados.")
        return

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
            _render_pontos(dia.pontos, chart_key=f"hist_ponto_{i}")
            _render_produtos_por_marca(dia.produtos, key_prefix=f"hist_prod_{i}")
            _render_mix_charts(
                dia.palcos,
                dia.formas_pagamento,
                dia.categorias,
                key_prefix=f"hist_mix_{i}",
            )
            _render_saida_horaria(
                dia.periodo,
                dia.saidas_horarias,
                nested=True,
                key_prefix=f"hist_saida_{i}",
            )


def main() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(255,255,255,0.02);
            border-radius: 12px;
        }
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

    _render_produtos_por_marca(snap.produtos, key_prefix="prod_atual")
    _render_mix_charts(
        snap.palcos,
        snap.formas_pagamento,
        snap.categorias,
        key_prefix="mix_atual",
    )

    saida_bucket = int(agora.timestamp() // SAIDA_HORARIA_TTL_SECONDS)
    with st.spinner("Carregando saída horária na janela operacional..."):
        try:
            periodo_saida, _horas, saidas = carregar_saida_horaria(
                cfg["login"], cfg["password"], cfg["evento_id"], saida_bucket
            )
            _render_saida_horaria(periodo_saida, saidas)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Não foi possível carregar a saída horária agora: {exc}")

    with st.spinner("Carregando dias oficiais anteriores..."):
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
