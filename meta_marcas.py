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

# Metas do print de referência (anexo original), já no rateio 70/20/10.
# Ex.: 06/09 Sirene = R$ 77.533,51 (10% de R$ 775.335,06).
META_VERSAO = "print-anexo-4"

META_POR_DIA_MARCA: dict[date, dict[str, float]] = {
    date(2026, 9, 2): {
        "Espetto": 42_408.90,
        "Mane": 12_117.12,
        "Sirene": 6_058.80,
    },
    date(2026, 9, 4): {
        "Espetto": 524_677.14,
        "Mane": 149_913.47,
        "Sirene": 74_956.87,
    },
    date(2026, 9, 5): {
        "Espetto": 511_774.54,
        "Mane": 146_221.44,
        "Sirene": 73_109.22,
    },
    date(2026, 9, 6): {
        "Espetto": 542_734.54,
        "Mane": 155_067.01,
        "Sirene": 77_533.51,
    },
    date(2026, 9, 7): {
        "Espetto": 457_050.32,
        "Mane": 130_585.87,
        "Sirene": 65_292.82,
    },
    date(2026, 9, 11): {
        "Espetto": 648_317.47,
        "Mane": 185_233.56,
        "Sirene": 92_616.78,
    },
    date(2026, 9, 12): {
        "Espetto": 551_836.14,
        "Mane": 157_671.18,
        "Sirene": 78_830.58,
    },
    date(2026, 9, 13): {
        "Espetto": 543_228.85,
        "Mane": 155_207.22,
        "Sirene": 77_603.62,
    },
}

META_POR_DIA_TOTAL: dict[date, float] = {
    d: round(sum(vals.values()), 2) for d, vals in META_POR_DIA_MARCA.items()
}


def meta_marca_dia(dia: date, marca: str) -> float:
    por_marca = META_POR_DIA_MARCA.get(dia)
    if por_marca and marca in por_marca:
        return float(por_marca[marca])
    total = META_POR_DIA_TOTAL.get(dia, 0.0)
    return round(total * RATEIO_MARCA.get(marca, 0.0), 2)


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

    linhas: list[LinhaMetaDia] = []
    for d in DIAS_OFICIAIS:
        inicio_janela = datetime(d.year, d.month, d.day, 12, 0, tzinfo=TZ)
        ocorreu = agora >= inicio_janela
        meta_total = META_POR_DIA_TOTAL.get(d, 0.0)
        celulas: dict[str, CelulaMeta] = {}

        if not ocorreu:
            for m in MARCAS:
                celulas[m] = CelulaMeta(realizado=None, meta=meta_marca_dia(d, m))
            total = CelulaMeta(realizado=None, meta=meta_total)
        else:
            real = realizados_por_dia.get(d, {})
            for m in MARCAS:
                celulas[m] = CelulaMeta(
                    realizado=float(real.get(m, 0.0)),
                    meta=meta_marca_dia(d, m),
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
