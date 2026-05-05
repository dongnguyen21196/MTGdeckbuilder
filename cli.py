#!/usr/bin/env python3
"""
cli.py — EDH Deck Builder CLI entry point.

Usage:
  python cli.py import --csv my_collection.csv
  python cli.py import --api
  python cli.py collection --list
  python cli.py build --output ranked --top 5
  python cli.py build --commander "Atraxa, Praetors' Voice" --output decklist
  python cli.py build --commander "Atraxa, Praetors' Voice" --output swap
  python cli.py build --commander "Atraxa, Praetors' Voice" --output buylist
  python cli.py build --output ranked --include-unowned-commanders --top 10
  python cli.py update --banned-list
  python cli.py update --commanders
"""

import sys
import os
import click
from dotenv import load_dotenv

load_dotenv()

# Ensure project root on path
sys.path.insert(0, os.path.dirname(__file__))

from db import cache
from enrichers import scryfall


@click.group()
def cli():
    """EDH Deck Builder — build optimized Commander decks from your collection."""
    cache.init_db()


# ── import ────────────────────────────────────────────────────────────────────

@cli.command("import")
@click.option("--csv", "csv_path", default=None, help="Path tới Archidekt CSV export")
@click.option("--api", is_flag=True, default=False, help="Import từ Archidekt API")
def import_collection(csv_path, api):
    """Import collection từ Archidekt CSV hoặc API."""
    if not csv_path and not api:
        click.echo("Cần chỉ định --csv <file> hoặc --api")
        sys.exit(1)

    cards = []

    if csv_path:
        from importers.archidekt_csv import parse_csv
        click.echo(f"Đang parse {csv_path}...")
        cards = parse_csv(csv_path)
        click.echo(f"  {len(cards)} card entries từ CSV")

    elif api:
        from importers.archidekt_api import fetch_collection
        cards = fetch_collection()

    if not cards:
        click.echo("Không có card nào được import.")
        sys.exit(1)

    cache.upsert_collection(cards)
    total_unique = len({c["name"] for c in cards})
    total_qty = sum(c["quantity"] for c in cards)
    click.echo(f"\nImport thành công: {total_unique} unique cards, {total_qty} total copies.")

    # Enrich với Scryfall (oracle data + prices)
    click.echo("\nEnriching với Scryfall data...")
    names = list({c["name"] for c in cards})
    scryfall.enrich_cards(names)

    # FIX 1: Cập nhật oracle_name mapping sau khi có Scryfall data
    cache.refresh_collection_oracle_names()
    click.echo("Scryfall enrichment hoàn thành. Oracle names đã được normalize.")


# ── collection ────────────────────────────────────────────────────────────────

@cli.command("collection")
@click.option("--list", "do_list", is_flag=True, default=False)
@click.option("--stats", is_flag=True, default=False)
def collection_cmd(do_list, stats):
    """Xem collection đã import."""
    rows = cache.get_collection()
    if not rows:
        click.echo("Collection trống. Chạy: python cli.py import --csv <file>")
        return

    if do_list:
        from tabulate import tabulate
        table = [[r["name"], r["quantity"]] for r in rows]
        click.echo(tabulate(table, headers=["Card", "Qty"], tablefmt="simple"))

    if stats or not do_list:
        total_unique = len(rows)
        total_qty = sum(r["quantity"] for r in rows)
        click.echo(f"\nCollection: {total_unique} unique cards, {total_qty} total copies.")


# ── build ─────────────────────────────────────────────────────────────────────

@cli.command("build")
@click.option("--commander", "-c", default=None, help="Tên commander cụ thể")
@click.option(
    "--output", "-o",
    type=click.Choice(["ranked", "decklist", "swap", "buylist"]),
    default="ranked",
    show_default=True,
)
@click.option("--top", default=5, show_default=True, help="Số deck trong output ranked")
@click.option("--include-unowned-commanders", is_flag=True, default=False,
              help="Gợi ý cả commander chưa có trong collection")
@click.option("--partner", default=None, help="Tên commander thứ hai (cho partner pairs)")
@click.option("--save", default=None, help="Lưu output ra file (decklist/buylist)")
def build_cmd(commander, output, top, include_unowned_commanders, partner, save):
    """Build và gợi ý EDH deck từ collection."""
    from engine.deck_builder import build_deck

    if commander:
        # Build deck cụ thể
        slug = scryfall._to_slug(commander)
        click.echo(f"\nBuilding deck cho {commander}" + (f" + {partner}" if partner else "") + "...")
        deck = build_deck(commander, slug, partner_name=partner)

        if output == "decklist":
            from outputs.decklist import print_decklist, export_decklist, default_filename
            if save:
                export_decklist(deck, save)
            else:
                print_decklist(deck)

        elif output == "swap":
            from outputs.swap import print_swaps
            print_swaps(deck)

        elif output == "buylist":
            from outputs.buylist import print_buylist
            buylist_path = save
            if not buylist_path and save:
                buylist_path = save
            print_buylist(deck, output_path=buylist_path)

        elif output == "ranked":
            from outputs.ranked import print_ranked
            print_ranked([deck], top_n=1)

    else:
        # Auto-pick commanders
        from engine.commander_picker import pick_commanders
        owned_only = not include_unowned_commanders

        mode_str = "collection" if owned_only else "all legal commanders"
        click.echo(f"\nScoring commanders từ {mode_str}...")

        commander_scores = pick_commanders(top_n=top, owned_only=owned_only)

        if not commander_scores:
            click.echo("Không tìm thấy commander phù hợp.")
            sys.exit(1)

        click.echo(f"Building {len(commander_scores)} decks...\n")
        decks = []
        for cs in commander_scores:
            click.echo(f"  Building: {cs.name}...")
            deck = build_deck(cs.name, cs.slug)
            decks.append(deck)

        from outputs.ranked import print_ranked
        print_ranked(decks, top_n=top)


# ── update ────────────────────────────────────────────────────────────────────

@cli.command("update")
@click.option("--banned-list", is_flag=True, default=False)
@click.option("--commanders", is_flag=True, default=False)
@click.option("--clear-edhrec-cache", is_flag=True, default=False)
@click.option("--refresh-prices", is_flag=True, default=False,
              help="Force refresh giá tất cả card trong collection (bỏ qua TTL)")
@click.option("--db-stats", is_flag=True, default=False,
              help="Hiển thị thống kê database")
def update_cmd(banned_list, commanders, clear_edhrec_cache, refresh_prices, db_stats):
    """Update data từ external sources."""
    if banned_list:
        scryfall.fetch_banned_list()
        click.echo("Banned list đã được update.")

    if commanders:
        scryfall.fetch_all_commanders()
        click.echo("Commander list đã được update.")

    if clear_edhrec_cache:
        with cache.get_conn() as conn:
            conn.execute("DELETE FROM edhrec_data")
        click.echo("EDHREC cache đã được xóa.")

    if refresh_prices:
        # Xóa price cache để force refetch toàn bộ
        with cache.get_conn() as conn:
            deleted = conn.execute("DELETE FROM scryfall_prices").rowcount
        click.echo(f"Đã xóa {deleted} price entries. Sẽ được refresh lần build tiếp theo.")
        # Trigger enrich ngay cho collection hiện tại
        collection_names = list(cache.get_collection_raw_names())
        if collection_names:
            click.echo(f"Refreshing prices cho {len(collection_names)} cards...")
            scryfall.enrich_cards(collection_names)
            click.echo("Price refresh hoàn thành.")

    if db_stats:
        stats = cache.get_db_stats()
        click.echo("\nDatabase stats:")
        for k, v in stats.items():
            click.echo(f"  {k:<20} {v}")

    if not any([banned_list, commanders, clear_edhrec_cache, refresh_prices, db_stats]):
        click.echo("Cần chỉ định ít nhất một option. Xem --help")


if __name__ == "__main__":
    cli()
