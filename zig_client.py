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


def _match_ponto_canonico(nome: str) -> str | None:
    n = nome.upper().replace(" ", "")
    if "ANTE" in n:
        return None
    for _, canon in PONTOS_SAIDA_HORARIA:
        c = canon.upper().replace(" ", "")
        if n == c or c in n or n in c:
            return canon
    return None


def montar_saida_horaria(
    horas_labels: list[str],
    produtos_por_hora: dict[str, list[ItemValor]],
    pontos_por_hora: dict[str, list[PontoVenda]],
) -> list[SaidaHorariaPonto]:
    """
    O Zig não filtra produto por ponto no resumo_evento.
    Rateamos a saída horária do produto entre os pontos da mesma marca
    (e bebidas entre todos) pela participação do ponto no faturamento da hora.
    """
    # estrutura vazia
    saidas: dict[str, SaidaHorariaPonto] = {}
    for palco, ponto in PONTOS_SAIDA_HORARIA:
        saidas[ponto] = SaidaHorariaPonto(
            palco=palco,
            ponto=ponto,
            marca=marca_do_ponto(ponto),
            horas=list(horas_labels),
            matriz={},
        )

    for hora in horas_labels:
        pontos = pontos_por_hora.get(hora, [])
        # mapa faturamento por ponto canônico (soma se houver variação de nome)
        fat_ponto: dict[str, float] = {p: 0.0 for p in saidas}
        for p in pontos:
            canon = _match_ponto_canonico(p.nome)
            if canon:
                fat_ponto[canon] = fat_ponto.get(canon, 0.0) + p.total

        fat_por_marca: dict[str, float] = {}
        for ponto, fat in fat_ponto.items():
            m = marca_do_ponto(ponto)
            fat_por_marca[m] = fat_por_marca.get(m, 0.0) + fat
        fat_total = sum(fat_ponto.values())

        for prod in produtos_por_hora.get(hora, []):
            marca_prod = marca_do_produto(prod.nome)
            for ponto, slot in saidas.items():
                marca_pt = slot.marca
                if marca_prod in {"Bebidas", "Outros"}:
                    base = fat_total
                    fat = fat_ponto.get(ponto, 0.0)
                elif marca_prod == marca_pt:
                    base = fat_por_marca.get(marca_pt, 0.0)
                    fat = fat_ponto.get(ponto, 0.0)
                else:
                    continue
                if base <= 0 or fat <= 0 or prod.quantidade == 0:
                    qtd = 0.0
                else:
                    qtd = prod.quantidade * (fat / base)
                if prod.nome not in slot.matriz:
                    slot.matriz[prod.nome] = {h: 0.0 for h in horas_labels}
                slot.matriz[prod.nome][hora] = slot.matriz[prod.nome].get(hora, 0.0) + qtd

    # remove produtos zerados em todas as horas
    result: list[SaidaHorariaPonto] = []
    for _palco, ponto in PONTOS_SAIDA_HORARIA:
        slot = saidas[ponto]
        slot.matriz = {
            prod: vals
            for prod, vals in slot.matriz.items()
            if sum(vals.values()) > 0.05
        }
        # ordena produtos pelo total
        slot.matriz = dict(
            sorted(
                slot.matriz.items(),
                key=lambda kv: sum(kv[1].values()),
                reverse=True,
            )
        )
        result.append(slot)
    return result


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

            fat = parse_faturamento_resumo_evento(html_dia)
            qtd, valor_pontos, pontos = parse_resumo_ponto(html_pontos)
            produtos = parse_produtos_vendidos(html_dia)
            formas = parse_formas_pagamento(html_dia)
            categorias = parse_consumo_categoria(html_cat)
            palcos = agregar_palcos(pontos)

            itens = sum(p.quantidade for p in produtos) if produtos else qtd
            if fat <= 0 and valor_pontos > 0:
                fat = valor_pontos
            ticket = (fat / qtd) if qtd else 0.0

            return DiaOperacional(
                label=label,
                periodo=periodo,
                inicio=inicio,
                fim=fim,
                faturamento=fat,
                transacoes=qtd,
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
    ) -> tuple[str, list[str], list[SaidaHorariaPonto]]:
        """
        Saída horária de produtos por ponto, somente na janela operacional atual
        (12:00–07:00), para economizar consultas.
        """
        agora = _aware(agora or datetime.now(TZ))
        dia_ini, dia_fim = janela_operacional(agora)
        faixas = iter_horas_janela(dia_ini, dia_fim)
        self.login_session()

        horas_labels: list[str] = []
        produtos_por_hora: dict[str, list[ItemValor]] = {}
        pontos_por_hora: dict[str, list[PontoVenda]] = {}

        for h_ini, h_fim in faixas:
            label = label_hora(h_ini)
            periodo = _fmt_periodo(h_ini, h_fim)
            horas_labels.append(label)
            try:
                html_evt = self.process_report(
                    "resumo_evento", [f"field-periodo={periodo}"]
                )
                html_pto = self.process_report(
                    "resumo_ponto", [f"field-periodo={periodo}"]
                )
                produtos_por_hora[label] = parse_produtos_vendidos(html_evt)
                _, _, pontos = parse_resumo_ponto(html_pto)
                pontos_por_hora[label] = pontos
            except Exception:
                produtos_por_hora[label] = []
                pontos_por_hora[label] = []

        saidas = montar_saida_horaria(horas_labels, produtos_por_hora, pontos_por_hora)
        periodo_label = _fmt_periodo(dia_ini, dia_fim)
        return periodo_label, horas_labels, saidas

    def fetch_historico(
        self,
        inicio_evento: datetime | None = None,
        agora: datetime | None = None,
    ) -> list[DiaOperacional]:
        agora = _aware(agora or datetime.now(TZ))
        self.login_session()
        historico: list[DiaOperacional] = []
        for ini, fim in listar_janelas_anteriores(
            agora, inicio_evento, apenas_dias_oficiais=True
        ):
            historico.append(self._metricas_periodo(ini, fim))
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
