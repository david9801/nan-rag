#!/usr/bin/env python3
"""
RFC RAG — NaN Builders cluster
Uso:
  python -m src.main ingest                      # indexa todos los RFCs del catálogo
  python -m src.main ingest rfc6749 rfc7519      # indexa solo los indicados
  python -m src.main ask                         # modo interactivo
  python -m src.main ask "¿Qué es PKCE?"        # pregunta directa
  python -m src.main list                        # muestra RFCs disponibles
"""

import sys
import chromadb
from rich.console import Console
from rich.table import Table

from .config import DB_PATH, RFCS
from .ingestion import ingest
from .query import ask

console = Console()


def get_collection() -> chromadb.Collection:
    db = chromadb.PersistentClient(path=DB_PATH)
    return db.get_or_create_collection(
        name="rfcs",
        metadata={"hnsw:space": "cosine"},
    )


def cmd_list():
    table = Table(title="RFCs disponibles para indexar", show_lines=True)
    table.add_column("ID",          style="cyan",  no_wrap=True)
    table.add_column("Título",      style="white")
    table.add_column("Descripción", style="dim")

    for rfc_id, meta in RFCS.items():
        table.add_row(rfc_id, meta["title"], meta["description"])

    console.print(table)
    console.print("\nPara añadir más RFCs edita [bold]src/config.py[/bold]")


def cmd_ingest(rfc_ids: list[str]):
    collection = get_collection()
    available  = list(RFCS.keys())

    if not rfc_ids:
        console.print(f"[yellow]Indexando todos los RFCs: {available}[/yellow]")
        rfc_ids = available
    else:
        invalid = [r for r in rfc_ids if r not in RFCS]
        if invalid:
            console.print(f"[red]RFCs no reconocidos: {invalid}[/red]")
            console.print(f"Disponibles: {available}")
            sys.exit(1)

    ingest(rfc_ids, collection)
    count = collection.count()
    console.print(f"\n[bold green]✅ Base de datos lista — {count} chunks totales[/bold green]")


def cmd_ask(question: str | None):
    collection = get_collection()
    count      = collection.count()

    if count == 0:
        console.print("[red]La base de datos está vacía. Ejecuta primero:[/red]")
        console.print("  python -m src.main ingest")
        sys.exit(1)

    console.print(f"[dim]Base de datos: {count} chunks de {len(RFCS)} RFCs posibles[/dim]")
    console.print("[dim]Escribe 'salir' para terminar, o filtra por RFC con '@rfc6749 <pregunta>'[/dim]\n")

    def process(q: str):
        rfc_filter = None
        if q.startswith("@"):
            parts = q.split(" ", 1)
            rfc_filter = parts[0][1:]
            q = parts[1] if len(parts) > 1 else ""
            console.print(f"[dim]Filtrando por: {rfc_filter}[/dim]")

        if not q.strip():
            return

        console.rule()
        ask(q, collection, rfc_filter=rfc_filter)
        console.print()
        console.rule()

    if question:
        process(question)
    else:
        while True:
            try:
                q = console.input("\n[bold cyan]Pregunta[/bold cyan] › ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Hasta luego[/dim]")
                break

            if q.lower() in ("salir", "exit", "quit", "q"):
                break

            process(q)


def main():
    args = sys.argv[1:]

    if not args or args[0] == "list":
        cmd_list()

    elif args[0] == "ingest":
        cmd_ingest(args[1:])

    elif args[0] == "ask":
        question = " ".join(args[1:]) if len(args) > 1 else None
        cmd_ask(question)

    else:
        console.print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
