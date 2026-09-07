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

# Meta TOTAL por dia oficial — curva do anexo de referência.
# Dias já ocorridos: valores do print (batem com realizado/% do anexo).
# Dias futuros: mesma proporção relativa do print, reescalados para
# a soma de todos os dias fechar exatamente META_EVENTO.
_META_DIA_OCORRIDOS: dict[date, float] = {
    date(2026, 9, 2): 60_606.21,
    date(2026, 9, 4): 748_896.22,
    date(2026, 9, 5): 644_674.20,
    date(2026, 9, 6): 810_203.70,
    date(2026, 9, 7): 895_808.33,
}
_META_DIA_FUTUROS_REF: dict[date, float] = {
    date(2026, 9, 11): 914_530.41,
    date(2026, 9, 12): 796_544.51,
    date(2026, 9, 13): 774_604.96,
}


def _metas_do_anexo() -> dict[date, float]:
    out: dict[date, float] = {d: 0.0 for d in DIAS_OFICIAIS}
    for d, v in _META_DIA_OCORRIDOS.items():
        if d in out:
            out[d] = float(v)

    soma_passados = sum(_META_DIA_OCORRIDOS.get(d, 0.0) for d in DIAS_OFICIAIS)
    resto = META_EVENTO - soma_passados
    futuros = [d for d in DIAS_OFICIAIS if d in _META_DIA_FUTUROS_REF]
    pesos = {d: float(_META_DIA_FUTUROS_REF[d]) for d in futuros}
    soma_pesos = sum(pesos.values())
    if futuros and soma_pesos > 0 and resto > 0:
        for d in futuros[:-1]:
            out[d] = round(resto * (pesos[d] / soma_pesos), 2)
        out[futuros[-1]] = round(resto - sum(out[d] for d in futuros[:-1]), 2)
    elif DIAS_OFICIAIS:
        # fallback: fecha no último dia
        out[DIAS_OFICIAIS[-1]] = round(
            META_EVENTO - sum(out[d] for d in DIAS_OFICIAIS[:-1]), 2
        )
    return out


META_POR_DIA_TOTAL: dict[date, float] = _metas_do_anexo()


def meta_marca_dia(dia: date, marca: str) -> float:
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
