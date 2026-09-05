"""Cliente do backoffice Zig/netPDV para extração de vendas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
BASE = "https://netpdv.com/backoffice"
EVENTO_INICIO_DEFAULT = datetime(2026, 8, 29, 8, 0, tzinfo=TZ)


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
    periodo_total: str = ""
    periodo_dia: str = ""
    historico: list[DiaOperacional] = field(default_factory=list)
    erro: str | None = None


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
        # Antes das 07:00: janela em andamento (ontem 12h → hoje 07h).
        # Entre 07:00 e 11:59: último dia operacional já encerrado.
        fim = agora.replace(hour=7, minute=0, second=0, microsecond=0)
        inicio = (fim - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

    if fim > agora:
        fim = agora
    return inicio, fim


def listar_janelas_anteriores(
    agora: datetime | None = None,
    inicio_evento: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """
    Janelas 12:00–07:00 já encerradas antes da janela operacional atual,
    da mais recente para a mais antiga.
    """
    agora = _aware(agora or datetime.now(TZ))
    inicio_evento = _aware(inicio_evento or EVENTO_INICIO_DEFAULT)
    atual_ini, _ = janela_operacional(agora)

    # Primeira abertura às 12:00 no dia do início do evento (ou no mesmo dia se já passou das 12h).
    first = inicio_evento.replace(hour=12, minute=0, second=0, microsecond=0)
    if first < inicio_evento:
        first = first + timedelta(days=1)

    janelas: list[tuple[datetime, datetime]] = []
    cursor = first
    while cursor < atual_ini:
        fim_completo = (cursor + timedelta(days=1)).replace(
            hour=7, minute=0, second=0, microsecond=0
        )
        if fim_completo <= atual_ini:
            janelas.append((cursor, fim_completo))
        cursor = (cursor + timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )

    janelas.reverse()
    return janelas


def label_janela(inicio: datetime, fim: datetime) -> str:
    return f"{inicio.strftime('%d/%m/%Y')} 12:00 - {fim.strftime('%d/%m/%Y')} 07:00"


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
            # Prefer the numeric cell (usually 3rd)
            for cell in reversed(cells):
                val = _parse_br_number(cell)
                if val or cell.strip() in {"0", "0,00"}:
                    return val
    # Fallback: first green table destaque
    m = re.search(
        r"Total Recebido ou Movimenta(?:&ccedil;|ç)(?:&atilde;|ã)o Financeira.*?"
        r"([\d.]+,\d{2})",
        html,
        re.I | re.S,
    )
    if m:
        return _parse_br_number(m.group(1))
    return 0.0


def parse_itens_produtos_vendidos(html: str) -> float:
    """Soma quantidades da seção Produtos Vendidos (quando disponível)."""
    soup = _soup(html)
    header = None
    for h in soup.find_all(["h3", "h4"]):
        if "produtos vendidos" in h.get_text(" ", strip=True).lower():
            header = h
            break
    if not header:
        return 0.0

    total = 0.0
    for sib in header.find_all_next():
        if sib.name in {"h3", "h4"} and sib is not header:
            break
        if sib.name != "tr":
            continue
        cells = [c.get_text(" ", strip=True) for c in sib.find_all("td")]
        if len(cells) < 2:
            continue
        # típico: Nome | Qtd | Valor
        nome = cells[0].lower()
        if not nome or "total" in nome:
            continue
        qtd = _parse_br_number(cells[1])
        if qtd > 0:
            total += qtd
    return total


def parse_resumo_ponto(html: str) -> tuple[float, float, list[PontoVenda]]:
    """
    Retorna (quantidade_total, valor_total, pontos ordenados por valor desc).
    Usa a seção CONSUMO do relatório Resumo por Ponto.
    """
    text = _soup(html).get_text("\n", strip=True)
    pontos: list[PontoVenda] = []

    # Linhas: NOME  QTD  VALOR  %  OPS  MEDIA
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

    # Prefer totals declared in header when present
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
        # Confirma sessão carregando home
        home = self.session.get(f"{BASE}/", timeout=60)
        home.raise_for_status()
        if "Authentication/Login" in home.url and "Relatórios" not in home.text and "Relatorios" not in home.text:
            # Ainda assim pode estar ok se title for ZIG - Relatórios
            if "vchLoginUsuario" in home.text and "form-login" in home.text:
                raise RuntimeError("Falha no login do Zig/netPDV. Verifique usuário e senha.")

    def process_report(self, report: str, fields: list[str]) -> str:
        data: dict[str, Any] = {
            "id": self.evento_id,
            "report": report,
            "isMobile": "",
        }
        # ASP.NET MVC model binder aceita fields / fields[i]
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
            fat = parse_faturamento_resumo_evento(html_dia)
            qtd, valor_pontos, pontos = parse_resumo_ponto(html_pontos)
            itens = parse_itens_produtos_vendidos(html_dia)
            if itens <= 0:
                itens = qtd
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

    def fetch_historico(
        self,
        inicio_evento: datetime | None = None,
        agora: datetime | None = None,
    ) -> list[DiaOperacional]:
        agora = _aware(agora or datetime.now(TZ))
        inicio_evento = _aware(inicio_evento or EVENTO_INICIO_DEFAULT)
        self.login_session()
        historico: list[DiaOperacional] = []
        for ini, fim in listar_janelas_anteriores(agora, inicio_evento):
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
                for ini, fim in listar_janelas_anteriores(agora, inicio_evento):
                    historico.append(self._metricas_periodo(ini, fim))

            return SnapshotVendas(
                gerado_em=agora,
                faturamento_total=fat_total,
                faturamento_dia=dia_atual.faturamento,
                transacoes_dia=dia_atual.transacoes,
                ticket_medio_dia=dia_atual.ticket_medio,
                itens_dia=dia_atual.itens,
                pontos=dia_atual.pontos,
                periodo_total=periodo_total,
                periodo_dia=periodo_dia,
                historico=historico,
                erro=dia_atual.erro,
            )
        except Exception as exc:  # noqa: BLE001 — superfície no dashboard
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
