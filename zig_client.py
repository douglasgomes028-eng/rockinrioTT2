"""Cliente do backoffice Zig/netPDV para extração de vendas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
BASE = "https://netpdv.com/backoffice"
EVENTO_INICIO_DEFAULT = datetime(2026, 8, 29, 8, 0, tzinfo=TZ)

# Dias oficiais de evento (abertura da janela 12:00)
DIAS_OFICIAIS: tuple[date, ...] = (
    date(2026, 9, 2),
    date(2026, 9, 4),
    date(2026, 9, 5),
    date(2026, 9, 6),
    date(2026, 9, 7),
    date(2026, 9, 11),
    date(2026, 9, 12),
    date(2026, 9, 13),
)

# Apex-like palette (referência visual)
COR_AZUL = "#008FFB"
COR_VERDE = "#00E396"
COR_LARANJA = "#FEB019"
COR_ROSA = "#FF4560"
CORES_PALCO = {"Mundo": COR_AZUL, "Sunset": COR_LARANJA}
CORES_CATEGORIA = {"Bebida": COR_VERDE, "Comida": COR_LARANJA}


@dataclass
class ItemValor:
    nome: str
    quantidade: float = 0.0
    total: float = 0.0


@dataclass
class PontoVenda:
    nome: str
    quantidade: float
    total: float


@dataclass
class DiaOperacional:
    """Métricas de uma janela operacional 12:00–07:00."""

    label: str
    periodo: str
    inicio: datetime
    fim: datetime
    faturamento: float
    transacoes: float
    ticket_medio: float
    itens: float
    pontos: list[PontoVenda] = field(default_factory=list)
    produtos: list[ItemValor] = field(default_factory=list)
    formas_pagamento: list[ItemValor] = field(default_factory=list)
    palcos: list[ItemValor] = field(default_factory=list)
    categorias: list[ItemValor] = field(default_factory=list)
    saidas_horarias: list[SaidaHorariaPonto] = field(default_factory=list)
    erro: str | None = None


@dataclass
class SnapshotVendas:
    gerado_em: datetime
    faturamento_total: float
    faturamento_dia: float
    transacoes_dia: float
    ticket_medio_dia: float
    itens_dia: float
    pontos: list[PontoVenda] = field(default_factory=list)
    produtos: list[ItemValor] = field(default_factory=list)
    formas_pagamento: list[ItemValor] = field(default_factory=list)
    palcos: list[ItemValor] = field(default_factory=list)
    categorias: list[ItemValor] = field(default_factory=list)
    periodo_total: str = ""
    periodo_dia: str = ""
    historico: list[DiaOperacional] = field(default_factory=list)
    erro: str | None = None


@dataclass
class SaidaHorariaPonto:
    """Tabela produto × hora para um ponto (saída estimada na janela operacional)."""

    palco: str
    ponto: str
    marca: str
    horas: list[str]
    # produto -> {hora_label: quantidade}
    matriz: dict[str, dict[str, float]] = field(default_factory=dict)


# Pontos principais exibidos na saída horária (como no briefing)
PONTOS_SAIDA_HORARIA: tuple[tuple[str, str], ...] = (
    ("Mundo", "MUN.A.ESPETTO.AEB03"),
    ("Mundo", "MUN.A.MANE.AEB04"),
    ("Mundo", "MUN.A.SIRENE.AEB05"),
    ("Sunset", "SUN.A.ESPETTO"),
)


def _parse_br_number(text: str) -> float:
    if not text:
        return 0.0
    cleaned = (
        text.replace("\xa0", " ")
        .strip()
        .replace("%", "")
        .replace("R$", "")
        .strip()
    )
    if not cleaned or cleaned in {"-", "—", "&nbsp;"}:
        return 0.0
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _fmt_periodo(inicio: datetime, fim: datetime) -> str:
    return f"{inicio.strftime('%d/%m/%Y %H:%M')} - {fim.strftime('%d/%m/%Y %H:%M')}"


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def janela_operacional(agora: datetime | None = None) -> tuple[datetime, datetime]:
    """Dia operacional: 12:00 até 07:00 do dia seguinte (horário de Brasília)."""
    agora = _aware(agora or datetime.now(TZ))

    if agora.hour >= 12:
        inicio = agora.replace(hour=12, minute=0, second=0, microsecond=0)
        fim = (inicio + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    else:
        fim = agora.replace(hour=7, minute=0, second=0, microsecond=0)
        inicio = (fim - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

    if fim > agora:
        fim = agora
    return inicio, fim


def listar_janelas_anteriores(
    agora: datetime | None = None,
    inicio_evento: datetime | None = None,
    apenas_dias_oficiais: bool = True,
) -> list[tuple[datetime, datetime]]:
    """
    Janelas 12:00–07:00 já encerradas antes da janela atual.
    Por padrão, só dias oficiais do cronograma.
    """
    agora = _aware(agora or datetime.now(TZ))
    atual_ini, _ = janela_operacional(agora)

    if apenas_dias_oficiais:
        candidatos = list(DIAS_OFICIAIS)
    else:
        inicio_evento = _aware(inicio_evento or EVENTO_INICIO_DEFAULT)
        first = inicio_evento.replace(hour=12, minute=0, second=0, microsecond=0)
        if first < inicio_evento:
            first = first + timedelta(days=1)
        candidatos = []
        cursor = first.date()
        while cursor <= atual_ini.date():
            candidatos.append(cursor)
            cursor = cursor + timedelta(days=1)

    janelas: list[tuple[datetime, datetime]] = []
    for d in candidatos:
        inicio = datetime(d.year, d.month, d.day, 12, 0, tzinfo=TZ)
        fim = (inicio + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
        if inicio < atual_ini and fim <= atual_ini:
            janelas.append((inicio, fim))

    janelas.sort(key=lambda x: x[0], reverse=True)
    return janelas


def label_janela(inicio: datetime, fim: datetime) -> str:
    return f"{inicio.strftime('%d/%m/%Y')} 12:00 - {fim.strftime('%d/%m/%Y')} 07:00"


def palco_do_ponto(nome: str) -> str:
    n = nome.upper()
    if n.startswith("SUN") or ".SUN" in n or n.startswith("SUN."):
        return "Sunset"
    if n.startswith("MUN") or n.startswith("MUNDO"):
        return "Mundo"
    return "Outros"


def marca_do_produto(nome: str) -> str:
    """Classifica produto nas marcas Impettus / bebidas."""
    n = nome.upper().strip()
    if n.startswith("(C)"):
        n = n[3:].strip()

    if "MANE" in n:
        return "Mane"
    if (
        "SIRENE" in n
        or "FISH" in n
        or "MIXED" in n
        or "MIEXD" in n
        or "SPRITZ" in n
        or "PIPOCA" in n
        or "PORCO" in n
        or "PORQUINHO" in n
    ):
        return "Sirene"
    if (
        "ESPETO" in n
        or "ESPS" in n
        or re.search(r"\bESP\b", n)
        or " ESP " in f" {n} "
        or "SAND" in n
        or n == "FRITAS"
        or n.startswith("FRITAS")
    ):
        return "Espetto"

    drink_kw = (
        "CHOPE",
        "HEINEKEN",
        "HNK",
        "COCA",
        "AGUA",
        "RED BULL",
        "LAGUN",
        "FANTA",
        "CERVEJA",
        "ULT",
        "IPA",
    )
    if n.startswith("RB") or any(k in n for k in drink_kw):
        return "Bebidas"
    return "Outros"


def marca_do_ponto(nome: str) -> str:
    n = nome.upper()
    if "MANE" in n:
        return "Mane"
    if "SIRENE" in n:
        return "Sirene"
    if "ESPETTO" in n or "ESPETO" in n:
        return "Espetto"
    return "Outros"


def iter_horas_janela(inicio: datetime, fim: datetime) -> list[tuple[datetime, datetime]]:
    """Fatias de 1h dentro da janela operacional já usada no app (12:00–07:00)."""
    inicio = _aware(inicio)
    fim = _aware(fim)
    if fim <= inicio:
        return []
    cursor = inicio.replace(minute=0, second=0, microsecond=0)
    faixas: list[tuple[datetime, datetime]] = []
    while cursor < fim:
        nxt = cursor + timedelta(hours=1)
        faixas.append((cursor, min(nxt, fim)))
        cursor = nxt
    return faixas


def label_hora(inicio: datetime) -> str:
    return inicio.strftime("%H:%M")


def iter_checkpoints_30min(inicio: datetime, fim: datetime) -> list[datetime]:
    """
    Marcos a cada 30 min na janela operacional.
    Ex.: 12:00, 12:30, 13:00, ... até cobrir o fim da janela.
    """
    inicio = _aware(inicio)
    fim = _aware(fim)
    if fim < inicio:
        return []
    pts: list[datetime] = []
    cursor = inicio.replace(second=0, microsecond=0)
    # alinha para :00 ou :30
    if cursor.minute not in (0, 30):
        if cursor.minute < 30:
            cursor = cursor.replace(minute=30)
        else:
            cursor = (cursor + timedelta(hours=1)).replace(minute=0)
    while cursor <= fim:
        pts.append(cursor)
        cursor = cursor + timedelta(minutes=30)
    # garante marco final no próximo half-hour se a janela ainda está aberta
    if not pts:
        pts.append(inicio.replace(second=0, microsecond=0))
    last = pts[-1]
    if last < fim:
        nxt = last + timedelta(minutes=30)
        pts.append(nxt)
    return pts


def normalizar_ponto_saida(nome: str) -> str | None:
    """Mapeia ponto (inclui ANTE) para o ponto principal do dashboard."""
    n = nome.upper().replace(" ", "")
    is_sun = n.startswith("SUN")
    if "MANE" in n:
        return "MUN.A.MANE.AEB04"
    if "SIRENE" in n:
        return "MUN.A.SIRENE.AEB05"
    if "ESPETTO" in n or "ESPETO" in n:
        return "SUN.A.ESPETTO" if is_sun else "MUN.A.ESPETTO.AEB03"
    for _, canon in PONTOS_SAIDA_HORARIA:
        c = canon.upper().replace(" ", "")
        if n == c:
            return canon
    return None


def _find_col(header: list[str], *candidates: str) -> int | None:
    norm = [" ".join(h.split()).lower() for h in header]
    for cand in candidates:
        c = cand.lower()
        for i, h in enumerate(norm):
            if h == c:
                return i
    for cand in candidates:
        c = cand.lower()
        for i, h in enumerate(norm):
            if c in h and not (c == "produto" and "categoria" in h):
                return i
    return None


def _parse_int_qty(text: str) -> int:
    if not text:
        return 0
    cleaned = text.replace("\xa0", "").strip().replace(".", "").replace(",", ".")
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return 0


def extrair_eventos_lista_transacao(html: str) -> list[tuple[datetime, str, str, int]]:
    """Extrai eventos (dt, ponto, produto, qtd_int) da Lista de Transações detalhada."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    i_op = _find_col(header, "Operação", "Operacao")
    i_data = _find_col(header, "Data Realização", "Data Realizacao")
    i_ponto = _find_col(header, "Nome Ponto")
    i_prod = _find_col(header, "Produto")
    i_qtd = _find_col(header, "Quantidade")
    i_status = _find_col(header, "Status")
    if None in {i_data, i_ponto, i_prod, i_qtd}:
        return []

    ops_ok = {"compra ficha", "retirada de produto", "cancelamento de ficha"}
    eventos: list[tuple[datetime, str, str, int]] = []

    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if i_prod >= len(cells) or i_ponto >= len(cells) or i_data >= len(cells):
            continue
        prod = cells[i_prod].strip()
        if not prod:
            continue
        if prod.upper().startswith("(C)"):
            prod = prod[3:].strip()
        op = (cells[i_op] if i_op is not None and i_op < len(cells) else "").strip()
        op_l = op.lower()
        if op_l not in ops_ok:
            continue
        status = (
            cells[i_status].strip().lower()
            if i_status is not None and i_status < len(cells)
            else ""
        )
        if status and status not in {"efetivada", "efetivado"}:
            continue

        ponto = normalizar_ponto_saida(cells[i_ponto])
        if not ponto:
            continue

        qtd = _parse_int_qty(cells[i_qtd] if i_qtd < len(cells) else "0")
        if qtd == 0:
            continue
        qtd = -abs(qtd) if "cancel" in op_l else abs(qtd)

        try:
            dt = datetime.strptime(cells[i_data].strip()[:16], "%d/%m/%Y %H:%M")
            dt = dt.replace(tzinfo=TZ)
        except ValueError:
            continue
        eventos.append((dt, ponto, prod, qtd))
    return eventos


def montar_saida_acumulada(
    eventos: list[tuple[datetime, str, str, int]],
    inicio: datetime,
    fim: datetime,
) -> list[SaidaHorariaPonto]:
    """
    Colunas a cada 30 min com total ACUMULADO desde o início da janela
    até aquele marco (ex.: 12:00=4, 12:30=25, 13:00=29).
    Venda às 13:15 entra a partir da coluna 13:30 (e seguintes).
    """
    inicio = _aware(inicio)
    fim = _aware(fim)
    checkpoints = iter_checkpoints_30min(inicio, fim)
    labels = [c.strftime("%H:%M") for c in checkpoints]
    if not labels:
        return montar_saida_horaria_vazia([])

    # ponto -> produto -> lista (dt, qtd)
    por_ponto: dict[str, dict[str, list[tuple[datetime, int]]]] = {
        ponto: {} for _, ponto in PONTOS_SAIDA_HORARIA
    }
    for dt, ponto, prod, qtd in eventos:
        if dt < inicio or dt > fim:
            continue
        por_ponto.setdefault(ponto, {}).setdefault(prod, []).append((dt, qtd))

    result: list[SaidaHorariaPonto] = []
    for palco, ponto in PONTOS_SAIDA_HORARIA:
        matriz: dict[str, dict[str, float]] = {}
        for prod, pares in por_ponto.get(ponto, {}).items():
            pares_sorted = sorted(pares, key=lambda x: x[0])
            col_vals: dict[str, float] = {}
            running = 0
            idx = 0
            for cp, label in zip(checkpoints, labels):
                while idx < len(pares_sorted) and pares_sorted[idx][0] <= cp:
                    running += pares_sorted[idx][1]
                    idx += 1
                col_vals[label] = float(max(0, running))
            if running > 0 or any(v > 0 for v in col_vals.values()):
                # só mantém se houve saída positiva em algum momento
                if any(v > 0 for v in col_vals.values()):
                    matriz[prod] = col_vals
        matriz = dict(
            sorted(
                matriz.items(),
                key=lambda kv: list(kv[1].values())[-1] if kv[1] else 0,
                reverse=True,
            )
        )
        result.append(
            SaidaHorariaPonto(
                palco=palco,
                ponto=ponto,
                marca=marca_do_ponto(ponto),
                horas=list(labels),
                matriz=matriz,
            )
        )
    return result


def montar_saida_horaria_vazia(horas_labels: list[str]) -> list[SaidaHorariaPonto]:
    return [
        SaidaHorariaPonto(
            palco=palco,
            ponto=ponto,
            marca=marca_do_ponto(ponto),
            horas=list(horas_labels),
            matriz={},
        )
        for palco, ponto in PONTOS_SAIDA_HORARIA
    ]


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def parse_faturamento_resumo_evento(html: str) -> float:
    soup = _soup(html)
    for tr in soup.select("tr.destaque"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if not cells:
            continue
        label = cells[0].lower()
        if "total recebido ou movimentação financeira" in label or "total recebido ou movimenta" in label:
            for cell in reversed(cells):
                val = _parse_br_number(cell)
                if val or cell.strip() in {"0", "0,00"}:
                    return val
    m = re.search(
        r"Total Recebido ou Movimenta(?:&ccedil;|ç)(?:&atilde;|ã)o Financeira.*?"
        r"([\d.]+,\d{2})",
        html,
        re.I | re.S,
    )
    if m:
        return _parse_br_number(m.group(1))
    return 0.0


def parse_formas_pagamento(html: str) -> list[ItemValor]:
    """Linhas de forma de pagamento no Resumo Financeiro (antes de Total Devoluções)."""
    soup = _soup(html)
    items: list[ItemValor] = []
    started = False
    for tr in soup.select("table.resumoEvento tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if not cells:
            continue
        label = cells[0].strip()
        label_l = label.lower()
        if "total recebido ou movimenta" in label_l and "subtraindo" not in label_l:
            started = True
            continue
        if not started:
            continue
        if "total devolu" in label_l or "subtraindo" in label_l:
            break
        if not label or label == "%":
            continue
        valor = 0.0
        for cell in cells[1:]:
            v = _parse_br_number(cell)
            if v > 0 or cell.strip() in {"0", "0,00"}:
                # pega o primeiro valor monetário relevante (não percentual já limpo)
                if "," in cell or cell.strip() in {"0", "0,00"}:
                    valor = v
                    break
        nome = label.replace("*", "").strip()
        if valor > 0:
            items.append(ItemValor(nome=nome, total=valor))
    items.sort(key=lambda x: x.total, reverse=True)
    return items


def parse_produtos_vendidos(html: str) -> list[ItemValor]:
    """
    Agrega produtos das tabelas Cashless + Fichas (ignora cancelamentos/ambulantes).
    """
    soup = _soup(html)
    header = None
    for h in soup.find_all(["h3", "h4"]):
        if "produtos vendidos" in h.get_text(" ", strip=True).lower():
            header = h
            break
    if not header:
        return []

    skip_headers = {
        "cashless",
        "fichas",
        "cancelamentos",
        "ambulantes",
        "total",
    }
    agg: dict[str, ItemValor] = {}
    capture = False

    for sib in header.find_all_next():
        if sib.name in {"h3", "h4"} and sib is not header:
            break
        if sib.name != "table":
            continue
        rows = sib.find_all("tr")
        if not rows:
            continue
        first_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
        if not first_cells:
            continue
        section = first_cells[0].strip().lower()
        if section in {"cashless", "fichas"}:
            capture = True
        elif section in {"cancelamentos", "ambulantes"}:
            capture = False
            continue
        if not capture:
            continue

        for tr in rows:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            nome = cells[0].strip()
            if not nome or nome.lower() in skip_headers:
                continue
            if nome.startswith("(C)"):
                continue
            qtd = _parse_br_number(cells[1])
            total = _parse_br_number(cells[2])
            if qtd == 0 and total == 0:
                continue
            key = nome.upper()
            if key in agg:
                agg[key].quantidade += qtd
                agg[key].total += total
            else:
                agg[key] = ItemValor(nome=nome, quantidade=qtd, total=total)

    produtos = list(agg.values())
    produtos.sort(key=lambda p: p.total, reverse=True)
    return produtos


def parse_itens_produtos_vendidos(html: str) -> float:
    return sum(p.quantidade for p in parse_produtos_vendidos(html))


def parse_consumo_categoria(html: str) -> list[ItemValor]:
    text = _soup(html).get_text("\n", strip=True)
    items: list[ItemValor] = []
    for m in re.finditer(
        r"^(Comida|Bebida)\s+([\d.]+(?:,\d+)?)\s+([\d.]+,\d{2})\s+",
        text,
        re.M | re.I,
    ):
        items.append(
            ItemValor(
                nome=m.group(1).title().replace("Comida", "Comida").replace("Bebida", "Bebida"),
                quantidade=_parse_br_number(m.group(2)),
                total=_parse_br_number(m.group(3)),
            )
        )
    # normaliza nomes
    for it in items:
        if it.nome.lower().startswith("comida"):
            it.nome = "Comida"
        elif it.nome.lower().startswith("bebida"):
            it.nome = "Bebida"
    items.sort(key=lambda x: x.total, reverse=True)
    return items


def parse_resumo_ponto(html: str) -> tuple[float, float, list[PontoVenda]]:
    text = _soup(html).get_text("\n", strip=True)
    pontos: list[PontoVenda] = []
    row_re = re.compile(
        r"^([A-Z0-9][A-Z0-9._\-\s]+?)\s+"
        r"([\d.]+(?:,\d+)?)\s+"
        r"([\d.]+,\d{2})\s+"
        r"([\d.]+(?:,\d+)?%)\s+"
        r"([\d.]+)\s+"
        r"([\d.]+(?:,\d+)?)\s*$",
        re.M,
    )
    for m in row_re.finditer(text):
        nome = m.group(1).strip()
        if nome.lower().startswith("total"):
            continue
        pontos.append(
            PontoVenda(
                nome=nome,
                quantidade=_parse_br_number(m.group(2)),
                total=_parse_br_number(m.group(3)),
            )
        )

    qtd_total = sum(p.quantidade for p in pontos)
    valor_total = sum(p.total for p in pontos)
    m_cons = re.search(
        r"CONSUMO\s*-\s*Quantidade:\s*([\d.]+(?:\,\d+)?)\s*Total:\s*([\d.]+,\d{2})",
        text,
        re.I,
    )
    if m_cons:
        qtd_total = _parse_br_number(m_cons.group(1))
        valor_total = _parse_br_number(m_cons.group(2))

    pontos.sort(key=lambda p: p.total, reverse=True)
    return qtd_total, valor_total, pontos


def agregar_palcos(pontos: list[PontoVenda]) -> list[ItemValor]:
    buckets: dict[str, ItemValor] = {}
    for p in pontos:
        palco = palco_do_ponto(p.nome)
        if palco not in buckets:
            buckets[palco] = ItemValor(nome=palco)
        buckets[palco].quantidade += p.quantidade
        buckets[palco].total += p.total
    items = [v for k, v in buckets.items() if k != "Outros" or v.total > 0]
    # Mundo / Sunset primeiro
    order = {"Mundo": 0, "Sunset": 1, "Outros": 2}
    items.sort(key=lambda x: (order.get(x.nome, 9), -x.total))
    return items


def produtos_por_marca(produtos: list[ItemValor]) -> dict[str, list[ItemValor]]:
    grupos: dict[str, list[ItemValor]] = {}
    for p in produtos:
        marca = marca_do_produto(p.nome)
        grupos.setdefault(marca, []).append(p)
    for marca in grupos:
        grupos[marca].sort(key=lambda x: x.total, reverse=True)
    # ordem estável de marcas
    ordem = ["Espetto", "Mane", "Sirene", "Bebidas", "Outros"]
    return {k: grupos[k] for k in ordem if k in grupos}


class ZigClient:
    def __init__(self, login: str, password: str, evento_id: int = 38049):
        self.login = login
        self.password = password
        self.evento_id = evento_id
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; ImpettusDashboard/1.0)",
            }
        )

    def login_session(self) -> None:
        r = self.session.get(f"{BASE}/Authentication/Login", timeout=60)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        token_el = soup.find("input", {"name": "__RequestVerificationToken"})
        if not token_el or not token_el.get("value"):
            raise RuntimeError("Token de login não encontrado no backoffice.")
        token = token_el["value"]
        r2 = self.session.post(
            f"{BASE}/Authentication/Login",
            data={
                "__RequestVerificationToken": token,
                "vchLoginUsuario": self.login,
                "vchSenha": self.password,
            },
            timeout=60,
            allow_redirects=True,
        )
        r2.raise_for_status()
        home = self.session.get(f"{BASE}/", timeout=60)
        home.raise_for_status()
        if "vchLoginUsuario" in home.text and "form-login" in home.text:
            raise RuntimeError("Falha no login do Zig/netPDV. Verifique usuário e senha.")

    def process_report(self, report: str, fields: list[str]) -> str:
        data: dict[str, Any] = {
            "id": self.evento_id,
            "report": report,
            "isMobile": "",
        }
        if len(fields) == 1:
            data["fields"] = fields[0]
        else:
            for i, f in enumerate(fields):
                data[f"fields[{i}]"] = f
        r = self.session.post(f"{BASE}/Relatorio/ProcessReport", data=data, timeout=120)
        if r.status_code == 302 or "form-login" in r.text:
            self.login_session()
            r = self.session.post(f"{BASE}/Relatorio/ProcessReport", data=data, timeout=120)
        r.raise_for_status()
        return r.text

    def fetch_dashboard_ficha_indicadores(
        self, periodo: str
    ) -> tuple[float, float, float]:
        """
        Indicadores do Dashboard Ficha (mesma fonte do Ticket Médio na Zig).

        Retorna (QtdVendas, ItensVendidos, receita_formas_pagamento).
        Ticket Médio Zig = receita_formas_pagamento / QtdVendas.
        """
        self.process_report("dashboard_ficha", [f"field-periodo={periodo}"])
        r = self.session.post(
            f"{BASE}/Relatorio/GetDashboardFichaData", data={}, timeout=120
        )
        if r.status_code == 302 or "form-login" in (r.text or ""):
            self.login_session()
            self.process_report("dashboard_ficha", [f"field-periodo={periodo}"])
            r = self.session.post(
                f"{BASE}/Relatorio/GetDashboardFichaData", data={}, timeout=120
            )
        r.raise_for_status()
        data = r.json()
        ind = (data.get("indicadores") or [None])[0] or {}
        qtd_vendas = float(ind.get("QtdVendas") or 0)
        itens = float(ind.get("ItensVendidos") or 0)
        receita = sum(
            float(x.get("Valor") or 0)
            for x in (data.get("receitaPorFormaPagamento") or [])
        )
        return qtd_vendas, itens, receita

    def _metricas_periodo(self, inicio: datetime, fim: datetime) -> DiaOperacional:
        periodo = _fmt_periodo(inicio, fim)
        label = label_janela(inicio, fim)
        try:
            html_dia = self.process_report(
                "resumo_evento", [f"field-periodo={periodo}"]
            )
            html_pontos = self.process_report(
                "resumo_ponto", [f"field-periodo={periodo}"]
            )
            html_cat = self.process_report(
                "consumo_categoria", [f"field-periodo={periodo}"]
            )
            qtd_vendas, itens_dash, receita_dash = self.fetch_dashboard_ficha_indicadores(
                periodo
            )

            fat = parse_faturamento_resumo_evento(html_dia)
            qtd_itens, valor_pontos, pontos = parse_resumo_ponto(html_pontos)
            produtos = parse_produtos_vendidos(html_dia)
            formas = parse_formas_pagamento(html_dia)
            categorias = parse_consumo_categoria(html_cat)
            palcos = agregar_palcos(pontos)

            # Alinha com a Zig: Ticket Médio = receita / QtdVendas
            if receita_dash > 0:
                fat = receita_dash
            elif fat <= 0 and valor_pontos > 0:
                fat = valor_pontos

            transacoes = qtd_vendas
            itens = (
                itens_dash
                if itens_dash > 0
                else (
                    sum(p.quantidade for p in produtos) if produtos else qtd_itens
                )
            )
            ticket = (fat / transacoes) if transacoes else 0.0

            return DiaOperacional(
                label=label,
                periodo=periodo,
                inicio=inicio,
                fim=fim,
                faturamento=fat,
                transacoes=transacoes,
                ticket_medio=ticket,
                itens=itens,
                pontos=pontos,
                produtos=produtos,
                formas_pagamento=formas,
                palcos=palcos,
                categorias=categorias,
            )
        except Exception as exc:  # noqa: BLE001
            return DiaOperacional(
                label=label,
                periodo=periodo,
                inicio=inicio,
                fim=fim,
                faturamento=0,
                transacoes=0,
                ticket_medio=0,
                itens=0,
                erro=str(exc),
            )

    def fetch_saida_horaria(
        self,
        agora: datetime | None = None,
        inicio: datetime | None = None,
        fim: datetime | None = None,
        *,
        ensure_login: bool = True,
    ) -> tuple[str, list[str], list[SaidaHorariaPonto]]:
        """
        Saída acumulada a cada 30 min por produto/ponto na janela 12:00–07:00.

        Busca a Lista de Transações hora a hora (o relatório diário truncado
        no HTML não traz todas as linhas).
        """
        agora = _aware(agora or datetime.now(TZ))
        if inicio is None or fim is None:
            dia_ini, dia_fim = janela_operacional(agora)
        else:
            dia_ini, dia_fim = _aware(inicio), _aware(fim)

        periodo_label = _fmt_periodo(dia_ini, dia_fim)
        checkpoints = iter_checkpoints_30min(dia_ini, dia_fim)
        labels = [c.strftime("%H:%M") for c in checkpoints]
        if ensure_login:
            self.login_session()

        if not labels:
            return periodo_label, [], montar_saida_horaria_vazia([])

        eventos: list[tuple[datetime, str, str, int]] = []
        for h_ini, h_fim in iter_horas_janela(dia_ini, dia_fim):
            periodo_hora = _fmt_periodo(h_ini, h_fim)
            try:
                html = self.process_report(
                    "lista_transacao",
                    [
                        f"field-periodo={periodo_hora}",
                        "field-tipo-relatorio-transacao=1",
                    ],
                )
                eventos.extend(extrair_eventos_lista_transacao(html))
            except Exception:
                continue

        saidas = montar_saida_acumulada(eventos, dia_ini, dia_fim)
        return periodo_label, labels, saidas

    def fetch_historico(
        self,
        inicio_evento: datetime | None = None,
        agora: datetime | None = None,
        incluir_saida_horaria: bool = True,
    ) -> list[DiaOperacional]:
        agora = _aware(agora or datetime.now(TZ))
        self.login_session()
        historico: list[DiaOperacional] = []
        for ini, fim in listar_janelas_anteriores(
            agora, inicio_evento, apenas_dias_oficiais=True
        ):
            dia = self._metricas_periodo(ini, fim)
            if incluir_saida_horaria and not dia.erro:
                try:
                    _periodo, _horas, saidas = self.fetch_saida_horaria(
                        inicio=ini,
                        fim=fim,
                        ensure_login=False,
                    )
                    dia.saidas_horarias = saidas
                except Exception:
                    dia.saidas_horarias = []
            historico.append(dia)
        return historico

    def fetch_snapshot(
        self,
        inicio_evento: datetime | None = None,
        agora: datetime | None = None,
        incluir_historico: bool = True,
    ) -> SnapshotVendas:
        agora = _aware(agora or datetime.now(TZ))
        inicio_evento = _aware(inicio_evento or EVENTO_INICIO_DEFAULT)
        dia_ini, dia_fim = janela_operacional(agora)
        periodo_total = _fmt_periodo(inicio_evento, agora)
        periodo_dia = _fmt_periodo(dia_ini, dia_fim)

        try:
            self.login_session()
            html_total = self.process_report(
                "resumo_evento", [f"field-periodo={periodo_total}"]
            )
            fat_total = parse_faturamento_resumo_evento(html_total)

            dia_atual = self._metricas_periodo(dia_ini, dia_fim)
            historico: list[DiaOperacional] = []
            if incluir_historico:
                for ini, fim in listar_janelas_anteriores(
                    agora, inicio_evento, apenas_dias_oficiais=True
                ):
                    historico.append(self._metricas_periodo(ini, fim))

            return SnapshotVendas(
                gerado_em=agora,
                faturamento_total=fat_total,
                faturamento_dia=dia_atual.faturamento,
                transacoes_dia=dia_atual.transacoes,
                ticket_medio_dia=dia_atual.ticket_medio,
                itens_dia=dia_atual.itens,
                pontos=dia_atual.pontos,
                produtos=dia_atual.produtos,
                formas_pagamento=dia_atual.formas_pagamento,
                palcos=dia_atual.palcos,
                categorias=dia_atual.categorias,
                periodo_total=periodo_total,
                periodo_dia=periodo_dia,
                historico=historico,
                erro=dia_atual.erro,
            )
        except Exception as exc:  # noqa: BLE001
            return SnapshotVendas(
                gerado_em=agora,
                faturamento_total=0,
                faturamento_dia=0,
                transacoes_dia=0,
                ticket_medio_dia=0,
                itens_dia=0,
                periodo_total=periodo_total,
                periodo_dia=periodo_dia,
                erro=str(exc),
            )
