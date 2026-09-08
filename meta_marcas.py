"""Metas por marca/dia (Rock In Rio 26 — Grupo Impettus)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from zig_client import DIAS_OFICIAIS, ItemValor, TZ, marca_do_produto

META_EVENTO = 5_475_486.96

MARCAS: tuple[str, ...] = ("Espetto", "Mane", "Sirene")

# Rateio de bebidas (e da meta consolidada por marca)
RATEIO_MARCA: dict[str, float] = {
    "Espetto": 0.70,
    "Mane": 0.20,
    "Sirene": 0.10,
}

META_VERSAO = "dinamica-saldo-v1"

# Soft opening — metas fixas do primeiro dia oficial
DIA_ABERTURA = date(2026, 9, 2)
META_FIXA_ABERTURA: dict[str, float] = {
    "Espetto": 42_409.93,
    "Mane": 12_117.12,
    "Sirene": 6_058.56,
}


def meta_marca_evento(marca: str) -> float:
    return round(META_EVENTO * RATEIO_MARCA.get(marca, 0.0), 2)


def agregar_realizado_por_marca(produtos: list[ItemValor]) -> dict[str, float]:
    """
    Realizado por marca com bebidas rateadas 70/20/10.
    Outros ficam de fora das colunas de marca.
    """
    buckets = {m: 0.0 for m in MARCAS}
    bebidas = 0.0
    for p in produtos:
        marca = marca_do_produto(p.nome)
        if marca == "Bebidas":
            bebidas += float(p.total)
        elif marca in buckets:
            buckets[marca] += float(p.total)
    for m, pct in RATEIO_MARCA.items():
        buckets[m] += bebidas * pct
    return {m: round(buckets[m], 2) for m in MARCAS}


def _inicio_janela(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=TZ)


def _dia_ja_iniciou(d: date, agora: datetime) -> bool:
    return agora >= _inicio_janela(d)


def _acumulado_antes(
    dia: date,
    realizados_por_dia: dict[date, dict[str, float]],
    agora: datetime,
) -> dict[str, float]:
    """Soma o realizado das marcas nos dias oficiais anteriores a `dia` que já começaram."""
    acum = {m: 0.0 for m in MARCAS}
    for d in DIAS_OFICIAIS:
        if d >= dia:
            break
        if not _dia_ja_iniciou(d, agora):
            continue
        real = realizados_por_dia.get(d, {})
        for m in MARCAS:
            acum[m] += float(real.get(m, 0.0))
    return {m: round(acum[m], 2) for m in MARCAS}


def calcular_metas_por_dia(
    realizados_por_dia: dict[date, dict[str, float]],
    agora: datetime | None = None,
) -> dict[date, dict[str, float]]:
    """
    02/09: metas fixas de abertura.
    Demais dias: (meta_marca − realizado acumulado anterior) / dias restantes
    (incluindo o próprio dia).

    Dias futuros ainda não iniciados compartilham o mesmo rateio igual do saldo
    restante à época do primeiro dia futuro.
    """
    agora = agora or datetime.now(TZ)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=TZ)
    else:
        agora = agora.astimezone(TZ)

    metas: dict[date, dict[str, float]] = {
        DIA_ABERTURA: {m: float(META_FIXA_ABERTURA[m]) for m in MARCAS}
    }

    for i, d in enumerate(DIAS_OFICIAIS):
        if d == DIA_ABERTURA:
            continue

        acum = _acumulado_antes(d, realizados_por_dia, agora)
        n_restantes = len(DIAS_OFICIAIS) - i
        if n_restantes <= 0:
            continue

        meta_dia = {
            m: round((meta_marca_evento(m) - acum[m]) / n_restantes, 2)
            for m in MARCAS
        }

        if not _dia_ja_iniciou(d, agora):
            # Congela o mesmo valor para todos os dias futuros restantes.
            for d_fut in DIAS_OFICIAIS[i:]:
                metas[d_fut] = dict(meta_dia)
            break

        metas[d] = meta_dia

    return metas


def meta_marca_dia(
    dia: date,
    marca: str,
    realizados_por_dia: dict[date, dict[str, float]] | None = None,
    agora: datetime | None = None,
) -> float:
    """Compat: devolve a meta dinâmica (ou a fixa do dia 02/09)."""
    if dia == DIA_ABERTURA:
        return float(META_FIXA_ABERTURA.get(marca, 0.0))
    mapa = calcular_metas_por_dia(realizados_por_dia or {}, agora=agora)
    return float(mapa.get(dia, {}).get(marca, 0.0))


@dataclass
class CelulaMeta:
    realizado: float | None  # None = ainda não ocorreu
    meta: float

    @property
    def pct(self) -> float | None:
        if self.realizado is None or self.meta <= 0:
            return None
        return (self.realizado / self.meta) * 100.0


@dataclass
class LinhaMetaDia:
    dia: date
    label: str
    celulas: dict[str, CelulaMeta]  # marca -> célula
    total: CelulaMeta
    ocorreu: bool


def montar_linhas_meta(
    realizados_por_dia: dict[date, dict[str, float]],
    agora: datetime | None = None,
) -> list[LinhaMetaDia]:
    """
    realizados_por_dia: date -> {Espetto, Mane, Sirene} com valores já rateados.
    Dias oficiais futuros (antes das 12:00 do dia) -> ainda não ocorreu.
    """
    agora = agora or datetime.now(TZ)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=TZ)
    else:
        agora = agora.astimezone(TZ)

    metas = calcular_metas_por_dia(realizados_por_dia, agora=agora)
    linhas: list[LinhaMetaDia] = []
    for d in DIAS_OFICIAIS:
        ocorreu = _dia_ja_iniciou(d, agora)
        meta_marcas = metas.get(d, {m: 0.0 for m in MARCAS})
        meta_total = round(sum(meta_marcas.values()), 2)
        celulas: dict[str, CelulaMeta] = {}

        if not ocorreu:
            for m in MARCAS:
                celulas[m] = CelulaMeta(realizado=None, meta=float(meta_marcas.get(m, 0.0)))
            total = CelulaMeta(realizado=None, meta=meta_total)
        else:
            real = realizados_por_dia.get(d, {})
            for m in MARCAS:
                celulas[m] = CelulaMeta(
                    realizado=float(real.get(m, 0.0)),
                    meta=float(meta_marcas.get(m, 0.0)),
                )
            tot_real = sum(celulas[m].realizado or 0.0 for m in MARCAS)
            total = CelulaMeta(realizado=round(tot_real, 2), meta=meta_total)

        linhas.append(
            LinhaMetaDia(
                dia=d,
                label=d.strftime("%d/%m/%Y"),
                celulas=celulas,
                total=total,
                ocorreu=ocorreu,
            )
        )
    return linhas


def linha_total_meta(linhas: list[LinhaMetaDia]) -> LinhaMetaDia:
    celulas: dict[str, CelulaMeta] = {}
    for m in MARCAS:
        real_sum = 0.0
        has_real = False
        for lin in linhas:
            c = lin.celulas[m]
            if c.realizado is not None:
                real_sum += c.realizado
                has_real = True
        celulas[m] = CelulaMeta(
            realizado=round(real_sum, 2) if has_real else None,
            meta=meta_marca_evento(m),
        )
    tot_real = None
    if any(celulas[m].realizado is not None for m in MARCAS):
        tot_real = round(sum(celulas[m].realizado or 0.0 for m in MARCAS), 2)
    return LinhaMetaDia(
        dia=DIAS_OFICIAIS[0],
        label="Total",
        celulas=celulas,
        total=CelulaMeta(realizado=tot_real, meta=META_EVENTO),
        ocorreu=True,
    )
